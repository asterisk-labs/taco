(() => {
  "use strict";

  const INDEX_NAME = "__cozip__";
  const PADDING_NAME = "__cozip_padding__";
  const INDEX_OFFSET = 51;
  const HASH_WINDOW_SIZE = 32768;
  const MIN_ARCHIVE_SIZE = HASH_WINDOW_SIZE + INDEX_OFFSET;
  const FORMAT_VERSION = 1;
  const PROFILE_NONE = 0;
  const ZIP32_MAX = 0xffffffff;
  const ZIP16_MAX = 0xffff;
  const GP_UTF8 = 1 << 11;
  const METHOD_STORE = 0;
  const DOS_TIME = 0;
  const DOS_DATE = 0x0021;
  const FNV_OFFSET = 0xcbf29ce484222325n;
  const FNV_PRIME = 0x100000001b3n;
  const U64_MASK = 0xffffffffffffffffn;
  const WASM_LOADER_URL = new URL("../../cozip/javascript/wasm/cozip.js", document.baseURI).href;
  const WASM_WRAPPER_URL = new URL("../../cozip/javascript/src/cozip-wasm-wrapper.js", document.baseURI).href;
  const WASM_BINARY_BASE_URL = new URL("../../cozip/javascript/wasm/", document.baseURI).href;

  const textEncoder = new TextEncoder();
  const crcTable = makeCrcTable();

  const state = {
    files: [],
    nextId: 1,
    objectUrl: null,
    lastResult: null,
    wasmPromise: null,
    wasmWriter: null,
    wasmError: null,
  };

  const els = {
    filePicker: document.getElementById("filePicker"),
    dropZone: document.getElementById("dropZone"),
    sampleBtn: document.getElementById("sampleBtn"),
    archiveName: document.getElementById("archiveName"),
    profileSelect: document.getElementById("profileSelect"),
    buildBtn: document.getElementById("buildBtn"),
    clearBtn: document.getElementById("clearBtn"),
    fileRows: document.getElementById("fileRows"),
    downloadLink: document.getElementById("downloadLink"),
    metrics: document.getElementById("metrics"),
    byteMap: document.getElementById("byteMap"),
    planList: document.getElementById("planList"),
    indexPreview: document.getElementById("indexPreview"),
    layoutRows: document.getElementById("layoutRows"),
    hashPreview: document.getElementById("hashPreview"),
    log: document.getElementById("log"),
    wasmStatus: document.getElementById("wasmStatus"),
  };

  init();

  function init() {
    primeWasm();
    bindEvents();
    renderFiles();
    renderEmptyOutput();
    setLog("Ready. Add files or load the sample payloads.");
  }

  function bindEvents() {
    els.filePicker.addEventListener("change", () => {
      addFiles(Array.from(els.filePicker.files).map((file) => ({ file })));
      els.filePicker.value = "";
    });

    els.sampleBtn.addEventListener("click", () => {
      addFiles(createSampleFiles(), { replace: true });
      setLog("Loaded deterministic sample files.");
    });

    els.buildBtn.addEventListener("click", () => {
      buildFromUi();
    });

    els.clearBtn.addEventListener("click", () => {
      state.files = [];
      state.lastResult = null;
      setDownload(null);
      renderFiles();
      renderEmptyOutput();
      setLog("Cleared input files and output archive.");
    });

    els.fileRows.addEventListener("input", (event) => {
      const input = event.target.closest(".path-input");
      if (!input) return;
      const item = findFile(input.dataset.id);
      if (item) item.name = input.value;
    });

    els.fileRows.addEventListener("change", (event) => {
      const toggle = event.target.closest(".index-toggle");
      if (!toggle) return;
      const item = findFile(toggle.dataset.id);
      if (item) item.inIndex = toggle.checked;
    });

    els.fileRows.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove]");
      if (!button) return;
      const id = button.dataset.remove;
      state.files = state.files.filter((item) => String(item.id) !== String(id));
      renderFiles();
      setLog("Removed one input file.");
    });

    for (const eventName of ["dragenter", "dragover"]) {
      els.dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        els.dropZone.classList.add("dragging");
      });
    }

    for (const eventName of ["dragleave", "drop"]) {
      els.dropZone.addEventListener(eventName, () => {
        els.dropZone.classList.remove("dragging");
      });
    }

    els.dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      const dropped = Array.from(event.dataTransfer.files).map((file) => ({ file }));
      addFiles(dropped);
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    });
  }

  function updateWasmStatus(status, detail = "") {
    els.wasmStatus.classList.remove("ok", "warn");
    els.wasmStatus.title = detail;

    if (status === "ready") {
      els.wasmStatus.textContent = "WASM ready";
      els.wasmStatus.classList.add("ok");
    } else if (status === "loading") {
      els.wasmStatus.textContent = "WASM loading";
    } else if (status === "fallback") {
      els.wasmStatus.textContent = "JS fallback";
      els.wasmStatus.classList.add("warn");
    } else {
      els.wasmStatus.textContent = "WASM unavailable";
      els.wasmStatus.classList.add("warn");
    }
  }

  function primeWasm() {
    if (!window.WebAssembly) {
      updateWasmStatus("unavailable", "This browser does not expose WebAssembly.");
      return;
    }

    updateWasmStatus("loading", "Loading the compiled cozip writer.");
    state.wasmPromise = loadWasmWriter()
      .then((writer) => {
        state.wasmWriter = writer;
        updateWasmStatus("ready", "Builds use the compiled C writer through WebAssembly.");
        return writer;
      })
      .catch((error) => {
        state.wasmError = error;
        updateWasmStatus("fallback", error.message);
        return null;
      });
  }

  async function getWasmWriter() {
    if (state.wasmWriter) return state.wasmWriter;
    if (!state.wasmPromise) return null;
    return state.wasmPromise;
  }

  async function loadWasmWriter() {
    await loadClassicScript(WASM_LOADER_URL);
    const wasm = await import(WASM_WRAPPER_URL);
    return wasm.createCozipWasmWriter({
      locateFile: (path) => new URL(path, WASM_BINARY_BASE_URL).href,
    });
  }

  function loadClassicScript(url) {
    if (typeof window.createCozipModule === "function") {
      return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = url;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Could not load ${url}`));
      document.head.append(script);
    });
  }

  function addFiles(items, options = {}) {
    if (options.replace) state.files = [];

    const seen = new Set(state.files.map((item) => item.name));
    let added = 0;
    let rejected = 0;

    for (const item of items) {
      const file = item.file;
      if (!file || typeof file.arrayBuffer !== "function") continue;

      if (file.size === 0) {
        rejected += 1;
        continue;
      }

      const rawName = item.name || file.name || `payload-${state.nextId}.bin`;
      const name = makeUniqueName(cleanArchiveName(rawName), seen);
      state.files.push({
        id: state.nextId++,
        file,
        name,
        inIndex: item.inIndex !== false,
      });
      added += 1;
    }

    renderFiles();
    state.lastResult = null;
    setDownload(null);
    renderEmptyOutput();

    if (added || rejected) {
      const parts = [];
      if (added) parts.push(`${added} file${added === 1 ? "" : "s"} added`);
      if (rejected) parts.push(`${rejected} zero-byte file${rejected === 1 ? "" : "s"} rejected`);
      setLog(`${parts.join("; ")}.`);
    }
  }

  async function buildFromUi() {
    if (!state.files.length) {
      setLog("Add at least one non-empty file before building.");
      return;
    }

    els.buildBtn.disabled = true;
    els.buildBtn.textContent = "Building...";

    try {
      const prepared = await prepareEntries();
      if (prepared.renamed.length) renderFiles();

      const profile = Number(els.profileSelect.value || PROFILE_NONE);
      const jsResult = buildCozipArchive(prepared.entries, profile);
      const built = await buildWithBestAvailableWriter(prepared.entries, profile, jsResult);
      const result = built.result;
      state.lastResult = result;
      setDownload(result);
      renderResult(result, prepared.renamed);
      setLog(buildSuccessMessage(result, built.engine, built.warning));
    } catch (error) {
      console.error(error);
      state.lastResult = null;
      setDownload(null);
      setLog(`Build failed: ${error.message}`);
    } finally {
      els.buildBtn.disabled = false;
      els.buildBtn.textContent = "Build cozip";
    }
  }

  async function prepareEntries() {
    const seen = new Set();
    const renamed = [];
    const entries = [];

    for (const item of state.files) {
      const previousName = item.name;
      const cleaned = cleanArchiveName(item.name || item.file.name);
      const name = makeUniqueName(cleaned, seen);
      if (name !== previousName) {
        renamed.push({ from: previousName, to: name });
        item.name = name;
      }

      const data = new Uint8Array(await item.file.arrayBuffer());
      if (data.length === 0) {
        throw new Error(`${name} is empty. cozip entries must have non-zero payloads.`);
      }
      assertZip32(data.length, `${name} payload`);

      const nameBytes = encodeName(name);
      entries.push({
        kind: "file",
        name,
        nameBytes,
        data,
        payloadSize: data.length,
        inIndex: item.inIndex,
      });
    }

    return { entries, renamed };
  }

  function buildCozipArchive(inputEntries, profile) {
    const entries = inputEntries.map((entry) => ({ ...entry }));
    const indexPayloadSize = computeIndexPayloadSize(entries);

    planEntries(entries, indexPayloadSize);
    let archiveSize = predictArchiveSize(entries, indexPayloadSize);

    let paddingEntry = null;
    if (archiveSize < MIN_ARCHIVE_SIZE) {
      const padNameBytes = encodeName(PADDING_NAME);
      const overhead = localHeaderSize(padNameBytes) + centralHeaderSize(padNameBytes);
      const paddingSize = Math.max(1, MIN_ARCHIVE_SIZE - archiveSize - overhead);
      paddingEntry = {
        kind: "padding",
        name: PADDING_NAME,
        nameBytes: padNameBytes,
        data: makePaddingBytes(paddingSize),
        payloadSize: paddingSize,
        inIndex: false,
      };
      entries.push(paddingEntry);
      planEntries(entries, indexPayloadSize);
      archiveSize = predictArchiveSize(entries, indexPayloadSize);
    }

    if (archiveSize < MIN_ARCHIVE_SIZE) {
      throw new Error("internal padding calculation did not reach the minimum cozip archive size");
    }

    const indexPayload = buildIndexPayload(entries, profile);
    const indexEntry = {
      kind: "index",
      name: INDEX_NAME,
      nameBytes: encodeName(INDEX_NAME),
      data: indexPayload,
      payloadSize: indexPayload.length,
      inIndex: false,
      lfhOffset: 0,
      lfhSize: INDEX_OFFSET,
      payloadOffset: INDEX_OFFSET,
    };

    indexEntry.crc = crc32(indexPayload);
    for (const entry of entries) {
      entry.crc = crc32(entry.data);
    }

    const parts = [];
    let cursor = 0;

    parts.push(makeLocalHeader(indexEntry, makeCozipExtraField()));
    cursor += parts[parts.length - 1].length;
    parts.push(indexPayload);
    cursor += indexPayload.length;

    for (const entry of entries) {
      if (cursor !== entry.lfhOffset) {
        throw new Error(`planned offset mismatch for ${entry.name}`);
      }
      const header = makeLocalHeader(entry, new Uint8Array(0));
      parts.push(header, entry.data);
      cursor += header.length + entry.data.length;
    }

    const cdOffset = cursor;
    const zipEntries = [indexEntry, ...entries];
    for (const entry of zipEntries) {
      const centralHeader = makeCentralHeader(entry);
      parts.push(centralHeader);
      cursor += centralHeader.length;
    }

    const cdSize = cursor - cdOffset;
    const eocdOffset = cursor;
    const eocd = makeEndOfCentralDirectory(zipEntries.length, cdSize, cdOffset);
    parts.push(eocd);
    cursor += eocd.length;

    const bytes = concatBytes(parts);
    if (bytes.length !== cursor) {
      throw new Error("archive byte assembly produced an unexpected length");
    }
    if (bytes.length !== archiveSize) {
      throw new Error("predicted archive size does not match assembled bytes");
    }

    const hash = computeIntegrityHash(bytes, indexPayload.length);
    putU64(bytes, 43, hash);

    const segments = buildSegments(entries, indexPayload.length, cdOffset, cdSize, eocdOffset, bytes.length);
    const indexedCount = entries.filter((entry) => entry.inIndex).length;

    return {
      bytes,
      indexPayload,
      indexEntry,
      entries,
      zipEntries,
      cdOffset,
      cdSize,
      eocdOffset,
      hash,
      hashHex: hex64(hash),
      paddingEntry,
      segments,
      profile,
      indexedCount,
      suffixStart: bytes.length - HASH_WINDOW_SIZE,
      indexEnd: INDEX_OFFSET + indexPayload.length,
    };
  }

  async function buildWithBestAvailableWriter(entries, profile, jsResult) {
    const writer = await getWasmWriter();
    if (!writer) {
      return {
        result: jsResult,
        engine: "JavaScript planner",
        warning: state.wasmError ? state.wasmError.message : "",
      };
    }

    try {
      const wasmBytes = await writer.writeArchive({
        entries: entries.map((entry) => ({
          name: entry.name,
          data: entry.data,
          inIndex: entry.inIndex,
        })),
        profile,
      });

      return {
        result: mergeWasmBytes(jsResult, wasmBytes),
        engine: "WASM C writer",
        warning: "",
      };
    } catch (error) {
      console.warn("WASM writer failed; falling back to JavaScript planner.", error);
      state.wasmError = error;
      updateWasmStatus("fallback", error.message);
      return {
        result: jsResult,
        engine: "JavaScript planner",
        warning: `WASM failed: ${error.message}`,
      };
    }
  }

  function mergeWasmBytes(modelResult, wasmBytes) {
    const result = { ...modelResult, bytes: wasmBytes, engine: "wasm" };
    if (wasmBytes.length >= INDEX_OFFSET + modelResult.indexPayload.length) {
      const hash = computeIntegrityHash(wasmBytes, modelResult.indexPayload.length);
      result.hash = hash;
      result.hashHex = hex64(hash);
      result.suffixStart = wasmBytes.length - HASH_WINDOW_SIZE;
      result.indexEnd = INDEX_OFFSET + modelResult.indexPayload.length;
    }
    if (wasmBytes.length !== modelResult.bytes.length) {
      result.wasmMismatch = `WASM output is ${formatBytes(wasmBytes.length)}; the JS visual plan is ${formatBytes(modelResult.bytes.length)}.`;
    }
    return result;
  }

  function buildSuccessMessage(result, engine, warning) {
    const parts = [
      `Built ${formatBytes(result.bytes.length)} archive with ${result.indexedCount} indexed file${result.indexedCount === 1 ? "" : "s"}`,
      `engine: ${engine}`,
    ];
    if (result.wasmMismatch) parts.push(result.wasmMismatch);
    if (warning) parts.push(warning);
    return `${parts.join("; ")}.`;
  }

  function computeIndexPayloadSize(entries) {
    let total = 11;
    for (const entry of entries) {
      if (!entry.inIndex) continue;
      total += 18 + entry.nameBytes.length;
    }
    assertZip32(total, "index payload");
    return total;
  }

  function planEntries(entries, indexPayloadSize) {
    let cursor = INDEX_OFFSET + indexPayloadSize;
    for (const entry of entries) {
      entry.lfhOffset = cursor;
      entry.lfhSize = localHeaderSize(entry.nameBytes);
      entry.payloadOffset = entry.lfhOffset + entry.lfhSize;
      entry.payloadSize = entry.data.length;
      cursor = entry.payloadOffset + entry.payloadSize;
      assertZip32(entry.lfhOffset, `${entry.name} local header offset`);
      assertZip32(entry.payloadOffset, `${entry.name} payload offset`);
    }
  }

  function predictArchiveSize(entries, indexPayloadSize) {
    let localEnd = INDEX_OFFSET + indexPayloadSize;
    for (const entry of entries) {
      localEnd = entry.payloadOffset + entry.payloadSize;
    }

    let centralSize = centralHeaderSize(encodeName(INDEX_NAME));
    for (const entry of entries) {
      centralSize += centralHeaderSize(entry.nameBytes);
    }

    assertZip32(localEnd, "Central Directory offset");
    assertZip32(centralSize, "Central Directory size");
    assertZip32(localEnd + centralSize + 22, "archive size");

    return localEnd + centralSize + 22;
  }

  function buildIndexPayload(entries, profile) {
    const indexed = entries.filter((entry) => entry.inIndex);
    const size = computeIndexPayloadSize(entries);
    const out = new Uint8Array(size);
    let cursor = 0;

    out.set(textEncoder.encode("CZIP"), cursor);
    cursor += 4;
    putU16(out, cursor, FORMAT_VERSION);
    cursor += 2;
    out[cursor++] = profile & 0xff;
    putU32(out, cursor, indexed.length);
    cursor += 4;

    for (const entry of indexed) {
      putU16(out, cursor, entry.nameBytes.length);
      cursor += 2;
    }

    for (const entry of indexed) {
      out.set(entry.nameBytes, cursor);
      cursor += entry.nameBytes.length;
    }

    for (const entry of indexed) {
      putU64(out, cursor, BigInt(entry.payloadOffset));
      cursor += 8;
    }

    for (const entry of indexed) {
      putU64(out, cursor, BigInt(entry.payloadSize));
      cursor += 8;
    }

    return out;
  }

  function makeCozipExtraField() {
    const out = new Uint8Array(12);
    putU16(out, 0, 0xca0c);
    putU16(out, 2, 8);
    return out;
  }

  function makeLocalHeader(entry, extra) {
    const size = 30 + entry.nameBytes.length + extra.length;
    const out = new Uint8Array(size);
    putU32(out, 0, 0x04034b50);
    putU16(out, 4, 20);
    putU16(out, 6, GP_UTF8);
    putU16(out, 8, METHOD_STORE);
    putU16(out, 10, DOS_TIME);
    putU16(out, 12, DOS_DATE);
    putU32(out, 14, entry.crc);
    putU32(out, 18, entry.payloadSize);
    putU32(out, 22, entry.payloadSize);
    putU16(out, 26, entry.nameBytes.length);
    putU16(out, 28, extra.length);
    out.set(entry.nameBytes, 30);
    out.set(extra, 30 + entry.nameBytes.length);
    return out;
  }

  function makeCentralHeader(entry) {
    const out = new Uint8Array(46 + entry.nameBytes.length);
    putU32(out, 0, 0x02014b50);
    putU16(out, 4, 20);
    putU16(out, 6, 20);
    putU16(out, 8, GP_UTF8);
    putU16(out, 10, METHOD_STORE);
    putU16(out, 12, DOS_TIME);
    putU16(out, 14, DOS_DATE);
    putU32(out, 16, entry.crc);
    putU32(out, 20, entry.payloadSize);
    putU32(out, 24, entry.payloadSize);
    putU16(out, 28, entry.nameBytes.length);
    putU16(out, 30, 0);
    putU16(out, 32, 0);
    putU16(out, 34, 0);
    putU16(out, 36, 0);
    putU32(out, 38, 0);
    putU32(out, 42, entry.lfhOffset);
    out.set(entry.nameBytes, 46);
    return out;
  }

  function makeEndOfCentralDirectory(entryCount, cdSize, cdOffset) {
    if (entryCount > ZIP16_MAX) {
      throw new Error("this playground does not implement ZIP64 entry counts");
    }
    assertZip32(cdSize, "Central Directory size");
    assertZip32(cdOffset, "Central Directory offset");

    const out = new Uint8Array(22);
    putU32(out, 0, 0x06054b50);
    putU16(out, 4, 0);
    putU16(out, 6, 0);
    putU16(out, 8, entryCount);
    putU16(out, 10, entryCount);
    putU32(out, 12, cdSize);
    putU32(out, 16, cdOffset);
    putU16(out, 20, 0);
    return out;
  }

  function buildSegments(entries, indexSize, cdOffset, cdSize, eocdOffset, archiveSize) {
    const segments = [
      { label: "__cozip__ LFH", start: 0, end: INDEX_OFFSET, kind: "index-lfh" },
      { label: "CZIP index", start: INDEX_OFFSET, end: INDEX_OFFSET + indexSize, kind: "index" },
    ];

    for (const entry of entries) {
      segments.push({
        label: entry.kind === "padding" ? "padding" : entry.name,
        start: entry.lfhOffset,
        end: entry.payloadOffset + entry.payloadSize,
        kind: entry.kind === "padding" ? "padding" : "file",
      });
    }

    segments.push({ label: "Central Directory", start: cdOffset, end: cdOffset + cdSize, kind: "cd" });
    segments.push({ label: "EOCD", start: eocdOffset, end: archiveSize, kind: "eocd" });
    return segments;
  }

  function computeIntegrityHash(bytes, indexSize) {
    const indexEnd = INDEX_OFFSET + indexSize;
    const suffixStart = bytes.length - HASH_WINDOW_SIZE;
    let hash = FNV_OFFSET;

    hash = fnv1aUpdate(hash, bytes, INDEX_OFFSET, indexEnd);
    if (indexEnd <= suffixStart) {
      hash = fnv1aUpdate(hash, bytes, suffixStart, bytes.length);
    } else {
      hash = fnv1aUpdate(hash, bytes, indexEnd, bytes.length);
    }

    return hash;
  }

  function fnv1aUpdate(hash, bytes, start, end) {
    let out = hash;
    for (let index = start; index < end; index += 1) {
      out ^= BigInt(bytes[index]);
      out = (out * FNV_PRIME) & U64_MASK;
    }
    return out;
  }

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (let index = 0; index < bytes.length; index += 1) {
      crc = (crc >>> 8) ^ crcTable[(crc ^ bytes[index]) & 0xff];
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  function makeCrcTable() {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let value = i;
      for (let bit = 0; bit < 8; bit += 1) {
        value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
      }
      table[i] = value >>> 0;
    }
    return table;
  }

  function setDownload(result) {
    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }

    if (!result) {
      els.downloadLink.href = "#";
      els.downloadLink.removeAttribute("download");
      els.downloadLink.classList.add("disabled");
      els.downloadLink.setAttribute("aria-disabled", "true");
      return;
    }

    const name = cleanDownloadName(els.archiveName.value);
    const blob = new Blob([result.bytes], { type: "application/zip" });
    state.objectUrl = URL.createObjectURL(blob);
    els.downloadLink.href = state.objectUrl;
    els.downloadLink.download = name;
    els.downloadLink.classList.remove("disabled");
    els.downloadLink.setAttribute("aria-disabled", "false");
  }

  function renderFiles() {
    els.fileRows.replaceChildren();

    if (!state.files.length) {
      const row = document.createElement("tr");
      row.className = "empty-row";
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = "No files loaded yet.";
      row.appendChild(cell);
      els.fileRows.appendChild(row);
      return;
    }

    for (const item of state.files) {
      const row = document.createElement("tr");

      const pathCell = document.createElement("td");
      const input = document.createElement("input");
      input.type = "text";
      input.value = item.name;
      input.spellcheck = false;
      input.className = "path-input";
      input.dataset.id = item.id;
      input.title = "Archive path";
      pathCell.appendChild(input);

      const sizeCell = document.createElement("td");
      sizeCell.className = "num";
      sizeCell.textContent = formatBytes(item.file.size);

      const indexCell = document.createElement("td");
      const switchLabel = document.createElement("label");
      switchLabel.className = "switch";
      switchLabel.title = "Include in byte-zero index";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "index-toggle";
      checkbox.dataset.id = item.id;
      checkbox.checked = item.inIndex;
      const toggleUi = document.createElement("span");
      toggleUi.className = "toggle-ui";
      switchLabel.append(checkbox, toggleUi);
      indexCell.appendChild(switchLabel);

      const actionCell = document.createElement("td");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-btn";
      remove.dataset.remove = item.id;
      remove.title = "Remove file";
      remove.setAttribute("aria-label", `Remove ${item.name}`);
      remove.innerHTML = "&times;";
      actionCell.appendChild(remove);

      row.append(pathCell, sizeCell, indexCell, actionCell);
      els.fileRows.appendChild(row);
    }
  }

  function renderEmptyOutput() {
    renderMetrics(null);
    renderByteMap(null);
    renderPlan(null);
    els.indexPreview.textContent = "No index yet.";
    els.hashPreview.textContent = "No hash yet.";
    els.layoutRows.replaceChildren(makeEmptyTableRow(4, "No layout yet."));
  }

  function renderResult(result, renamed) {
    renderMetrics(result);
    renderByteMap(result);
    renderPlan(result, renamed);
    renderIndexPreview(result);
    renderHashPreview(result);
    renderLayoutRows(result);
  }

  function renderMetrics(result) {
    const values = els.metrics.querySelectorAll("strong");
    if (!result) {
      values[0].textContent = "-";
      values[1].textContent = "-";
      values[2].textContent = "-";
      values[3].textContent = "-";
      return;
    }

    values[0].textContent = formatBytes(result.bytes.length);
    values[1].textContent = formatBytes(result.indexPayload.length);
    const userEntryCount = result.entries.filter((entry) => entry.kind === "file").length;
    values[2].textContent = `${result.indexedCount}/${userEntryCount}`;
    values[3].textContent = result.hashHex;
  }

  function renderByteMap(result) {
    els.byteMap.replaceChildren();
    if (!result) {
      const placeholder = document.createElement("span");
      placeholder.className = "byte-placeholder";
      placeholder.textContent = "Build an archive to see byte ranges.";
      els.byteMap.appendChild(placeholder);
      return;
    }

    for (const segment of result.segments) {
      const el = document.createElement("span");
      const length = Math.max(1, segment.end - segment.start);
      el.className = `byte-segment byte-${segment.kind}`;
      el.style.flex = `${Math.max(length, result.bytes.length * 0.012)} 1 0`;
      el.textContent = segment.label;
      el.title = `${segment.label}: [${segment.start}, ${segment.end})`;
      els.byteMap.appendChild(el);
    }
  }

  function renderPlan(result, renamed = []) {
    els.planList.replaceChildren();

    if (!result) {
      [
        "Choose one or more non-empty files.",
        "Mark the files that should appear in the byte-zero cozip index.",
        "Build the archive to compute Local File Header offsets, index payload, Central Directory, EOCD, and the final FNV-1a 64 hash.",
      ].forEach((step) => appendListItem(els.planList, step));
      return;
    }

    const indexFormula = explainIndexFormula(result.entries);
    const paddingText = result.paddingEntry
      ? `Added ${formatBytes(result.paddingEntry.payloadSize)} of padding so the archive reaches the ${formatBytes(MIN_ARCHIVE_SIZE)} cozip minimum.`
      : "No padding entry was needed because the archive already satisfies the cozip minimum size.";

    [
      "Normalized archive paths and rejected empty payloads.",
      `Computed the index payload size: ${indexFormula}.`,
      "Planned every user Local File Header before writing bytes, so indexed payload offsets are final.",
      "Serialized the CZIP index payload at archive byte 51.",
      paddingText,
      `Wrote ${result.zipEntries.length} ZIP entries, the Central Directory at byte ${result.cdOffset}, and EOCD at byte ${result.eocdOffset}.`,
      `Patched ${result.hashHex} into archive bytes 43..50 after hashing the index and final 32 KiB.`,
    ].forEach((step) => appendListItem(els.planList, step));

    for (const change of renamed) {
      appendListItem(els.planList, `Renamed ${change.from || "(empty)"} to ${change.to} for cozip path safety.`);
    }
  }

  function renderIndexPreview(result) {
    const indexed = result.entries.filter((entry) => entry.inIndex);
    const nameBytes = indexed.reduce((sum, entry) => sum + entry.nameBytes.length, 0);
    const lines = [
      "CZIP index payload",
      `archive range: [${INDEX_OFFSET}, ${INDEX_OFFSET + result.indexPayload.length})`,
      `magic: CZIP`,
      `version: ${FORMAT_VERSION}`,
      `profile: NONE (${result.profile})`,
      `n_entries: ${indexed.length}`,
      "",
      "section sizes",
      `header: 11 bytes`,
      `name lengths: ${indexed.length * 2} bytes`,
      `names: ${nameBytes} bytes`,
      `offsets: ${indexed.length * 8} bytes`,
      `sizes: ${indexed.length * 8} bytes`,
      "",
      "priority entries",
    ];

    if (!indexed.length) {
      lines.push("  (none)");
    }

    for (const entry of indexed) {
      lines.push(`  - ${entry.name}`);
      lines.push(`    offset: ${entry.payloadOffset}`);
      lines.push(`    size: ${entry.payloadSize}`);
    }

    els.indexPreview.textContent = lines.join("\n");
  }

  function renderHashPreview(result) {
    const overlap = Math.max(0, result.indexEnd - result.suffixStart);
    const lines = [
      "Integrity patch",
      "hash slot: archive bytes 43..50",
      `patched value: ${result.hashHex}`,
      "",
      "hash input",
      `index region: [${INDEX_OFFSET}, ${result.indexEnd})`,
      `suffix region: [${result.suffixStart}, ${result.bytes.length})`,
      `overlap skipped: ${overlap} bytes`,
      "",
      "note: FNV-1a 64 is a compact structural check, not an authentication layer.",
    ];
    els.hashPreview.textContent = lines.join("\n");
  }

  function renderLayoutRows(result) {
    els.layoutRows.replaceChildren();

    for (const entry of result.zipEntries) {
      const row = document.createElement("tr");
      const name = document.createElement("td");
      name.textContent = entry.kind === "padding" ? `${entry.name} (padding)` : entry.name;

      const lfh = document.createElement("td");
      lfh.className = "num";
      lfh.textContent = String(entry.lfhOffset);

      const payload = document.createElement("td");
      payload.className = "num";
      payload.textContent = String(entry.payloadOffset);

      const size = document.createElement("td");
      size.className = "num";
      size.textContent = String(entry.payloadSize);

      row.append(name, lfh, payload, size);
      els.layoutRows.appendChild(row);
    }
  }

  function appendListItem(list, text) {
    const item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  }

  function makeEmptyTableRow(columns, text) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = columns;
    cell.textContent = text;
    row.appendChild(cell);
    return row;
  }

  function selectTab(name) {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${name}`);
    });
  }

  function findFile(id) {
    return state.files.find((item) => String(item.id) === String(id));
  }

  function cleanArchiveName(raw) {
    let name = String(raw || "").replace(/\0/g, "").replace(/\\/g, "/").trim();
    name = name.replace(/^[a-zA-Z]:+/, "");
    name = name.replace(/^\/+/, "");

    const parts = name
      .split("/")
      .map((part) => part.trim())
      .filter((part) => part && part !== "." && part !== "..");

    name = parts.join("/");
    if (!name) name = "payload.bin";
    if (name === INDEX_NAME || name === PADDING_NAME) {
      name = `payload/${name}.bin`;
    }
    return name;
  }

  function makeUniqueName(name, seen) {
    let candidate = name;
    let counter = 2;
    while (seen.has(candidate)) {
      candidate = addNameSuffix(name, counter);
      counter += 1;
    }
    seen.add(candidate);
    return candidate;
  }

  function addNameSuffix(name, counter) {
    const slash = name.lastIndexOf("/");
    const dir = slash >= 0 ? `${name.slice(0, slash + 1)}` : "";
    const leaf = slash >= 0 ? name.slice(slash + 1) : name;
    const dot = leaf.lastIndexOf(".");
    if (dot > 0) {
      return `${dir}${leaf.slice(0, dot)}-${counter}${leaf.slice(dot)}`;
    }
    return `${dir}${leaf}-${counter}`;
  }

  function cleanDownloadName(raw) {
    const requested = String(raw || "").trim();
    const cleaned = cleanArchiveName(requested || "dataset.cozip.zip").replace(/\//g, "-");
    return cleaned || "dataset.cozip.zip";
  }

  function encodeName(name) {
    const bytes = textEncoder.encode(name);
    if (bytes.length === 0) {
      throw new Error("archive names must not be empty");
    }
    if (bytes.length > ZIP16_MAX) {
      throw new Error(`${name} is too long for a ZIP filename field`);
    }
    return bytes;
  }

  function localHeaderSize(nameBytes) {
    return 30 + nameBytes.length;
  }

  function centralHeaderSize(nameBytes) {
    return 46 + nameBytes.length;
  }

  function putU16(out, offset, value) {
    const v = Number(value);
    out[offset] = v & 0xff;
    out[offset + 1] = (v >>> 8) & 0xff;
  }

  function putU32(out, offset, value) {
    const v = Number(value) >>> 0;
    out[offset] = v & 0xff;
    out[offset + 1] = (v >>> 8) & 0xff;
    out[offset + 2] = (v >>> 16) & 0xff;
    out[offset + 3] = (v >>> 24) & 0xff;
  }

  function putU64(out, offset, value) {
    let v = BigInt(value);
    for (let index = 0; index < 8; index += 1) {
      out[offset + index] = Number(v & 0xffn);
      v >>= 8n;
    }
  }

  function concatBytes(parts) {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const out = new Uint8Array(total);
    let cursor = 0;
    for (const part of parts) {
      out.set(part, cursor);
      cursor += part.length;
    }
    return out;
  }

  function assertZip32(value, label) {
    if (value < 0 || value >= ZIP32_MAX) {
      throw new Error(`${label} exceeds the ZIP32 limit; the playground does not implement ZIP64`);
    }
  }

  function makePaddingBytes(size) {
    const out = new Uint8Array(size);
    out.fill(0x5a);
    return out;
  }

  function createSampleFiles() {
    const collection = JSON.stringify(
      {
        id: "demo-collection",
        title: "cozip playground sample",
        profile: "NONE",
        generated_by: "deck/playground",
      },
      null,
      2,
    );

    const metadata = [
      '{"name":"DATA/image_001.bin","offset":0,"size":4096}',
      '{"name":"DATA/image_002.bin","offset":0,"size":2048}',
      '{"name":"NOTES/readme.txt","offset":0,"size":112}',
      "",
    ].join("\n");

    return [
      {
        file: makeFile([textEncoder.encode(collection)], "COLLECTION.json", "application/json"),
        name: "COLLECTION.json",
        inIndex: true,
      },
      {
        file: makeFile([textEncoder.encode(metadata)], "items.ndjson", "application/x-ndjson"),
        name: "METADATA/items.ndjson",
        inIndex: true,
      },
      {
        file: makeFile([makePatternBytes(4096, 17)], "image_001.bin", "application/octet-stream"),
        name: "DATA/image_001.bin",
        inIndex: true,
      },
      {
        file: makeFile([makePatternBytes(2048, 91)], "image_002.bin", "application/octet-stream"),
        name: "DATA/image_002.bin",
        inIndex: false,
      },
      {
        file: makeFile([textEncoder.encode("This archive is built entirely in the browser.\n")], "readme.txt", "text/plain"),
        name: "NOTES/readme.txt",
        inIndex: false,
      },
    ];
  }

  function makeFile(parts, name, type) {
    try {
      return new File(parts, name, { type, lastModified: 0 });
    } catch (_error) {
      const blob = new Blob(parts, { type });
      blob.name = name;
      blob.lastModified = 0;
      return blob;
    }
  }

  function makePatternBytes(size, seed) {
    const out = new Uint8Array(size);
    let value = seed & 0xff;
    for (let index = 0; index < size; index += 1) {
      value = (value * 33 + index + seed) & 0xff;
      out[index] = value;
    }
    return out;
  }

  function explainIndexFormula(entries) {
    const indexed = entries.filter((entry) => entry.inIndex);
    const nameBytes = indexed.reduce((sum, entry) => sum + entry.nameBytes.length, 0);
    return `11 + (${indexed.length} * 18) + ${nameBytes} name bytes = ${computeIndexPayloadSize(entries)} bytes`;
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KiB", "MiB", "GiB"];
    let value = bytes / 1024;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }
    return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[unitIndex]}`;
  }

  function hex64(value) {
    return `0x${value.toString(16).padStart(16, "0")}`;
  }

  function setLog(message) {
    els.log.textContent = message;
  }
})();
