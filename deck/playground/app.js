import { openDataset } from "../../javascript/src/index.js";

const FIXTURE_ROOT = "https://huggingface.co/datasets/asterisk-labs/taco-api-fixtures/resolve/main";
const MANIFEST_URL = `${FIXTURE_ROOT}/manifest.json`;
const CENTROID_PROFILES = new Set(["stac", "stac-interval", "shared-stac", "istac"]);
const COLORS = { train: "#0f766e", validation: "#d97706", test: "#7c3aed" };

const element = Object.fromEntries(
  [
    "fixtureSelect", "datasetMetadata", "status", "message",
    "metadataPanel", "pointPosition", "pointTitle", "pointCoordinates", "metadataBody", "closeMetadata",
    "metadataPath", "metadataSource", "metadataCount", "loading",
  ].map((id) => [id, document.getElementById(id)]),
);

const map = L.map("map", { attributionControl: false, zoomControl: false, worldCopyJump: true }).setView([12, 0], 2);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 16,
}).addTo(map);
L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
  maxZoom: 16,
  pane: "overlayPane",
}).addTo(map);

const state = {
  manifest: null,
  fixtures: [],
  centroidCases: new Set(),
  fixtureIndex: -1,
  dataset: null,
  points: [],
  markers: [],
  selectedPoint: -1,
  panelMode: null,
  metadataPages: [],
  metadataPageIndex: -1,
  metadataToken: 0,
  messageTimer: null,
  loadToken: 0,
};

bindEvents();
initialize();

async function initialize() {
  setStatus("loading", "Loading fixtures");
  try {
    const response = await fetch(MANIFEST_URL);
    if (!response.ok) throw new Error(`Fixture manifest returned HTTP ${response.status}`);
    state.manifest = await response.json();
    state.fixtures = state.manifest.datasets || [];
    state.centroidCases = new Set(
      (state.manifest.logical_cases || [])
        .filter((item) => CENTROID_PROFILES.has(item.coordinate_profile))
        .map((item) => item.id),
    );
    if (state.fixtures.length !== 50) throw new Error(`Expected 50 fixtures, found ${state.fixtures.length}`);
    populateFixtureSelect();
    const first = state.fixtures.findIndex((item) => state.centroidCases.has(item.case));
    await loadFixture(first < 0 ? 0 : first);
  } catch (error) {
    fail(error);
  }
}

function bindEvents() {
  element.fixtureSelect.addEventListener("change", () => {
    const requested = Number(element.fixtureSelect.value);
    if (!Number.isInteger(requested)) return;
    const fixture = state.fixtures[requested];
    if (state.centroidCases.has(fixture.case)) {
      loadFixture(requested);
      return;
    }
    const next = findCompatibleFixture(requested, 1, fixture.topology);
    if (next < 0) return fail(new Error("No fixture with a sample-level STAC or ISTAC centroid was found."));
    showMessage(`${fixture.case} has no sample centroid. Opened ${state.fixtures[next].case}.`);
    loadFixture(next);
  });
  element.datasetMetadata.addEventListener("click", () => {
    if (state.panelMode === "dataset" && element.metadataPanel.classList.contains("open")) closeMetadata();
    else showDatasetMetadata();
  });
  element.closeMetadata.addEventListener("click", closeMetadata);
  document.addEventListener("keydown", (event) => {
    if (!element.metadataPanel.classList.contains("open")) return;
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLButtonElement) return;
    if (event.key === "Escape") closeMetadata();
    if (event.key === "ArrowLeft") navigateMetadata(-1);
    if (event.key === "ArrowRight") navigateMetadata(1);
  });
}

function populateFixtureSelect() {
  element.fixtureSelect.replaceChildren();
  const groups = new Map();
  state.fixtures.forEach((fixture, index) => {
    if (!state.centroidCases.has(fixture.case)) return;
    if (!groups.has(fixture.case)) groups.set(fixture.case, []);
    groups.get(fixture.case).push({ fixture, index });
  });
  for (const [caseId, fixtures] of groups) {
    const group = document.createElement("optgroup");
    group.label = caseId;
    for (const { fixture, index } of fixtures) {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${caseId} / ${topologyLabel(fixture.topology)}`;
      group.append(option);
    }
    element.fixtureSelect.append(group);
  }
}

async function loadFixture(index) {
  if (index < 0 || index >= state.fixtures.length) return;
  const token = ++state.loadToken;
  const fixture = state.fixtures[index];
  setLoading(true);
  closeMetadata();
  clearMarkers();
  state.points = [];
  setStatus("loading", "Reading TACO");
  disableDatasetNavigation(true);
  element.fixtureSelect.value = String(index);

  try {
    const dataset = await openDataset(fixtureUrl(fixture));
    const sampleFields = dataset.contract.metadata.sample || {};
    const centroidField = "stac:centroid" in sampleFields
      ? "stac:centroid"
      : "istac:centroid" in sampleFields
        ? "istac:centroid"
        : null;
    if (!centroidField) {
      const next = findCompatibleFixture(index, 1, fixture.topology);
      if (next >= 0 && next !== index) {
        showMessage(`${fixture.case} has no sample centroid. Moving to ${state.fixtures[next].case}.`);
        if (token === state.loadToken) await loadFixture(next);
        return;
      }
      throw new Error("This fixture has no sample-level STAC or ISTAC centroid.");
    }

    const rows = await dataset.read({ layout: "wide", location: false });
    const points = rows.flatMap((row) => {
      const centroid = decodeWkbPoint(row[centroidField]);
      return centroid ? [{ row, centroidField, longitude: centroid[0], latitude: centroid[1] }] : [];
    });
    if (!points.length) throw new Error(`The ${centroidField} column contains no readable points.`);
    if (token !== state.loadToken) return;

    state.fixtureIndex = index;
    state.dataset = dataset;
    state.points = points;
    renderDataset(fixture);
    renderPoints();
    setStatus("ready", `${points.length} points`);
  } catch (error) {
    if (token === state.loadToken) fail(error);
  } finally {
    if (token === state.loadToken) {
      setLoading(false);
      disableDatasetNavigation(false);
    }
  }
}

function renderDataset(fixture) {
  element.fixtureSelect.value = String(state.fixtureIndex);
}

function renderPoints() {
  clearMarkers();
  const bounds = [];
  state.points.forEach((point, index) => {
    const split = String(point.row["ml:split"] || "train");
    const color = COLORS[split] || COLORS.train;
    const marker = L.circleMarker([point.latitude, point.longitude], {
      radius: 7,
      color: "#ffffff",
      weight: 2,
      fillColor: color,
      fillOpacity: .94,
    });
    marker.bindTooltip(pointName(point), { direction: "top", offset: [0, -6] });
    marker.on("click", () => { void selectPoint(index); });
    marker.addTo(map);
    state.markers.push(marker);
    bounds.push([point.latitude, point.longitude]);
  });
  if (bounds.length === 1) map.setView(bounds[0], 7);
  else map.fitBounds(bounds, { padding: [70, 70], maxZoom: 5 });
}

async function selectPoint(index) {
  if (!state.points.length) return;
  const normalized = (index + state.points.length) % state.points.length;
  const token = ++state.metadataToken;
  state.selectedPoint = normalized;
  state.markers.forEach((marker, markerIndex) => {
    marker.setStyle(markerIndex === normalized
      ? { radius: 9, color: "#20251f", weight: 3 }
      : { radius: 7, color: "#ffffff", weight: 2 });
  });
  const point = state.points[normalized];
  map.panTo([point.latitude, point.longitude]);
  state.panelMode = "point";
  element.metadataPanel.classList.remove("dataset-mode");
  element.datasetMetadata.setAttribute("aria-expanded", "false");
  element.pointPosition.textContent = `Point ${normalized + 1} of ${state.points.length}`;
  element.pointTitle.textContent = pointName(point);
  element.pointCoordinates.textContent = `${formatLatitude(point.latitude)}, ${formatLongitude(point.longitude)}`;
  element.metadataPanel.classList.add("open");
  element.metadataPanel.setAttribute("aria-hidden", "false");
  renderMetadataLoading();

  try {
    const pages = await metadataPagesForPoint(point);
    if (token !== state.metadataToken || normalized !== state.selectedPoint) return;
    state.metadataPages = pages;
    state.metadataPageIndex = 0;
    renderMetadataNavigation();
    renderMetadataPage();
  } catch (error) {
    if (token !== state.metadataToken) return;
    element.metadataSource.textContent = "Metadata error";
    element.metadataCount.textContent = "";
    element.metadataBody.replaceChildren(metadataMessage(messageOf(error)));
  }
}

async function metadataPagesForPoint(point) {
  const sourceFile = point.row.source_file;
  const sampleId = Number(point.row.sample_id);
  const [levelRows, longRows] = await Promise.all([
    Promise.all(state.dataset.levels.map((level) => state.dataset.readLevel(level))),
    state.dataset.read({ layout: "long", idx: sampleId, location: true }),
  ]);

  const selectedLongRows = longRows.filter((row) => sourceFile === undefined || row.source_file === sourceFile);
  const locations = new Map(
    selectedLongRows.map((row) => [locationKey(row.source_file, row.path), row["taco:location"]]),
  );
  const pages = [];

  const selectedRows = new Map();
  const samples = levelRows[0].filter((row) =>
    Number(row["internal:current_id"]) === sampleId && sameSource(row["internal:source_file"], sourceFile),
  );
  selectedRows.set("sample", samples);
  pages.push(parquetPage("sample", samples, locations));

  for (let index = 1; index < state.dataset.levels.length; index += 1) {
    const level = state.dataset.levels[index];
    const parents = selectedRows.get(parentMetadataLevel(level)) ?? [];
    const parentIds = new Set(parents.map(rowIdentity));
    const rows = levelRows[index].filter((row) =>
      parentIds.has(parentIdentity(row)) && sameSource(row["internal:source_file"], sourceFile),
    );
    selectedRows.set(level, rows);
    pages.push(parquetPage(level, rows, locations));
  }
  return pages;
}

function parquetPage(level, rows, locations) {
  return {
    label: levelFilename(level),
    depth: metadataLevelDepth(level),
    records: rows.map((row, index) => {
      const path = contractPath(row["internal:relative_path"]);
      const values = {};
      if (path) values.path = path;
      values.current_id = row["internal:current_id"];
      if (row["internal:parent_id"] !== undefined) values.parent_id = row["internal:parent_id"];
      if (row["internal:source_file"] !== undefined) values.source_file = row["internal:source_file"];
      if (row["internal:offset"] !== undefined) values.offset = row["internal:offset"];
      if (row["internal:size"] !== undefined) values.size = row["internal:size"];
      for (const [key, value] of Object.entries(row)) {
        if (!key.startsWith("internal:")) values[key] = value;
      }
      const location = locations.get(locationKey(row["internal:source_file"], path));
      if (location) values["taco:location"] = location;
      return { title: path || String(row["fixture:sample_key"] ?? `row ${index}`), values };
    }),
  };
}

function renderMetadataLoading() {
  state.metadataPages = [];
  state.metadataPageIndex = -1;
  element.metadataPath.replaceChildren();
  element.metadataSource.textContent = "Reading hierarchy…";
  element.metadataCount.textContent = "";
  element.metadataBody.replaceChildren();
  element.metadataBody.append(metadataMessage("Reading Parquet metadata for this point…"));
}

function renderMetadataNavigation() {
  element.metadataPath.replaceChildren();
  state.metadataPages.forEach((page, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = page.label;
    button.style.setProperty("--depth", String(page.depth));
    button.setAttribute("aria-current", index === state.metadataPageIndex ? "page" : "false");
    button.addEventListener("click", () => {
      state.metadataPageIndex = index;
      renderMetadataNavigation();
      renderMetadataPage();
    });
    if (index === state.metadataPageIndex) button.classList.add("active");
    element.metadataPath.append(button);
  });
  element.metadataPath.querySelector("button.active")?.scrollIntoView({ block: "nearest", inline: "center" });
}

function renderMetadataPage() {
  const page = state.metadataPages[state.metadataPageIndex];
  if (!page) return;
  element.metadataSource.textContent = page.label;
  element.metadataCount.textContent = `${page.records.length} ${page.records.length === 1 ? "row" : "rows"}`;
  element.metadataBody.replaceChildren();
  if (!page.records.length) {
    element.metadataBody.append(metadataMessage("No row for this point at this level."));
    return;
  }
  page.records.forEach((record) => appendMetadataRecord(record));
}

function navigateMetadata(direction) {
  if (!state.metadataPages.length) return;
  const next = Math.max(0, Math.min(state.metadataPages.length - 1, state.metadataPageIndex + direction));
  if (next === state.metadataPageIndex) return;
  state.metadataPageIndex = next;
  renderMetadataNavigation();
  renderMetadataPage();
}

function appendMetadataRecord(record) {
  const section = document.createElement("section");
  section.className = "metadata-record";
  const heading = document.createElement("h3");
  heading.textContent = record.title;
  const list = document.createElement("dl");
  for (const [name, value] of orderedMetadataEntries(record.values)) {
    if (name === "stac:centroid" || name === "istac:centroid") continue;
    const wrapper = document.createElement("div");
    wrapper.className = "metadata-row";
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    if (name === "taco:location") {
      wrapper.classList.add("location-row");
      appendLocation(detail, String(value), String(record.values.path || record.title));
    } else {
      detail.textContent = formatValue(value);
    }
    wrapper.append(term, detail);
    list.append(wrapper);
  }
  section.append(heading, list);
  element.metadataBody.append(section);
}

function appendLocation(parent, location, filename) {
  const code = document.createElement("code");
  code.className = "location-value";
  code.textContent = location;
  const actions = document.createElement("div");
  actions.className = "file-actions";
  const result = document.createElement("span");
  result.className = "asset-result";

  const copy = fileAction("Copy location", result, async (button) => {
    await copyText(location);
    button.textContent = "Copied";
    result.textContent = "Location copied to clipboard.";
    window.setTimeout(() => { button.textContent = "Copy location"; }, 1600);
  });
  const download = fileAction("Download", result, async (button) => {
    button.textContent = "Preparing…";
    const resolved = state.dataset.resolveAsset(location);
    const blob = await resolved.blob(contentType(filename));
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = basename(filename) || "taco-asset";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    result.textContent = `${anchor.download} · ${formatBytes(blob.size)}`;
    button.textContent = "Download";
  });
  actions.append(copy, download);

  if (/\.rumi$/i.test(filename)) {
    const read = fileAction("Read Rumi", result, async (button) => {
      button.textContent = "Reading…";
      const resolved = state.dataset.resolveAsset(location);
      const bytes = new Uint8Array(await resolved.arrayBuffer());
      const magic = new TextDecoder("ascii").decode(bytes.subarray(0, 4));
      result.textContent = `${magic} · ${formatBytes(bytes.length)} · ${resolved.offset === null ? "direct" : `offset ${resolved.offset}`}`;
      button.textContent = "Read again";
    });
    actions.append(read);
  }

  parent.append(code, actions, result);
}

function fileAction(label, result, action) {
  const button = document.createElement("button");
  button.className = "file-action";
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", async () => {
    button.disabled = true;
    result.textContent = "";
    try {
      await action(button);
    } catch (error) {
      result.textContent = messageOf(error);
      button.textContent = label;
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function showDatasetMetadata() {
  if (!state.dataset || state.fixtureIndex < 0) return;
  state.metadataToken += 1;
  clearSelectedPoint();
  state.panelMode = "dataset";
  state.metadataPages = [];
  state.metadataPageIndex = -1;

  const fixture = state.fixtures[state.fixtureIndex];
  const collection = state.dataset.collection;
  element.metadataPanel.classList.add("dataset-mode", "open");
  element.metadataPanel.setAttribute("aria-hidden", "false");
  element.datasetMetadata.setAttribute("aria-expanded", "true");
  element.pointPosition.textContent = `Dataset · ${topologyLabel(fixture.topology)}`;
  element.pointTitle.textContent = String(collection.title || collection.id);
  element.metadataPath.replaceChildren();
  element.metadataSource.textContent = "COLLECTION.json";
  element.metadataCount.textContent = `${state.dataset.levels.length} metadata ${state.dataset.levels.length === 1 ? "level" : "levels"}`;
  element.metadataBody.replaceChildren();

  appendDatasetOverview(fixture, collection);
  appendContractGraph();
  appendContractSchemas();
}

function appendDatasetOverview(fixture, collection) {
  const providers = (collection.providers || []).map((provider) =>
    typeof provider === "string" ? provider : provider.name || provider.url || "provider",
  );
  appendMetadataRecord({
    title: "Collection",
    values: compactObject({
      id: collection.id,
      format_version: collection["taco:version"],
      dataset_version: collection.dataset_version,
      title: collection.title,
      description: collection.description,
      licenses: collection.licenses,
      providers,
      tasks: collection.tasks,
      samples: collection["taco:sources"]?.samples ?? state.points.length,
      partitions: collection["taco:sources"]?.partitions?.length,
      container: topologyLabel(fixture.topology),
      source: state.dataset.url,
    }),
  });

  const extent = collection.extent;
  if (extent && typeof extent === "object") {
    const spatial = extent.spatial?.bbox ?? extent.spatial;
    const temporal = extent.temporal?.interval ?? extent.temporal;
    const coverage = compactObject({
      spatial_bbox: Array.isArray(spatial) ? spatial.flat(1).join(", ") : spatial,
      temporal_interval: Array.isArray(temporal)
        ? temporal.map((interval) => Array.isArray(interval) ? interval.map((value) => value ?? "open").join(" → ") : interval ?? "open").join(" → ")
        : temporal,
    });
    if (Object.keys(coverage).length) appendMetadataRecord({ title: "Coverage", values: coverage });
  }
}

function appendContractGraph() {
  const section = document.createElement("section");
  section.className = "dataset-section";
  const heading = document.createElement("h3");
  heading.textContent = "Contract graph";
  const graph = document.createElement("div");
  graph.className = "contract-graph";

  state.dataset.levels.forEach((level) => {
    const fields = Object.keys(state.dataset.contract.metadata[level] || {}).length;
    graph.append(contractNode("▦", levelFilename(level), `${fields} ${fields === 1 ? "field" : "fields"}`, metadataLevelDepth(level)));
  });

  if (state.dataset.structure === null) {
    graph.append(contractNode("○", "No payload structure", "metadata only", 1, true));
  } else {
    state.dataset.structure.forEach((declaration) => {
      const depth = 2 + (declaration.match(/\//g)?.length ?? 0);
      graph.append(contractNode("◆", declaration, "payload", depth, true));
    });
  }
  section.append(heading, graph);
  element.metadataBody.append(section);
}

function contractNode(icon, label, detail, depth, payload = false) {
  const node = document.createElement("div");
  node.className = `contract-node${payload ? " payload" : ""}`;
  node.style.setProperty("--depth", String(depth));
  const mark = document.createElement("span");
  mark.className = "node-icon";
  mark.textContent = icon;
  const name = document.createElement("span");
  name.textContent = label;
  const description = document.createElement("span");
  description.className = "node-detail";
  description.textContent = detail;
  node.append(mark, name, description);
  return node;
}

function appendContractSchemas() {
  const section = document.createElement("section");
  section.className = "dataset-section";
  const heading = document.createElement("h3");
  heading.textContent = "Metadata schemas";
  section.append(heading);

  state.dataset.levels.forEach((level) => {
    const fields = Object.entries(state.dataset.contract.metadata[level] || {});
    const card = document.createElement("article");
    card.className = "schema-card";
    const header = document.createElement("header");
    const name = document.createElement("strong");
    name.textContent = levelFilename(level);
    const count = document.createElement("span");
    count.textContent = `${fields.length} ${fields.length === 1 ? "field" : "fields"}`;
    header.append(name, count);
    card.append(header);

    fields.forEach(([fieldName, declaration]) => {
      const field = document.createElement("div");
      field.className = "schema-field";
      const line = document.createElement("div");
      const code = document.createElement("code");
      code.textContent = fieldName;
      const type = document.createElement("span");
      type.textContent = `${declaration.type}${declaration.nullable ? " · nullable" : ""}`;
      line.append(code, type);
      const description = document.createElement("p");
      description.textContent = declaration.description;
      field.append(line, description);
      card.append(field);
    });
    section.append(card);
  });
  element.metadataBody.append(section);
}

function findCompatibleFixture(start, direction, preferredTopology) {
  for (let step = 1; step <= state.fixtures.length; step += 1) {
    const index = (start + direction * step + state.fixtures.length) % state.fixtures.length;
    const fixture = state.fixtures[index];
    if (state.centroidCases.has(fixture.case) && fixture.topology === preferredTopology) return index;
  }
  for (let step = 1; step <= state.fixtures.length; step += 1) {
    const index = (start + direction * step + state.fixtures.length) % state.fixtures.length;
    if (state.centroidCases.has(state.fixtures[index].case)) return index;
  }
  return -1;
}

function closeMetadata() {
  state.metadataToken += 1;
  element.metadataPanel.classList.remove("open");
  element.metadataPanel.setAttribute("aria-hidden", "true");
  element.datasetMetadata.setAttribute("aria-expanded", "false");
  clearSelectedPoint();
  state.panelMode = null;
  state.metadataPages = [];
  state.metadataPageIndex = -1;
}

function clearSelectedPoint() {
  if (state.selectedPoint >= 0 && state.markers[state.selectedPoint]) {
    state.markers[state.selectedPoint].setStyle({ radius: 7, color: "#ffffff", weight: 2 });
  }
  state.selectedPoint = -1;
}

function clearMarkers() {
  state.markers.forEach((marker) => marker.remove());
  state.markers = [];
}

function decodeWkbPoint(value) {
  let bytes;
  if (value instanceof Uint8Array) bytes = value;
  else if (value instanceof ArrayBuffer) bytes = new Uint8Array(value);
  else if (ArrayBuffer.isView(value)) bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  else return null;
  if (bytes.byteLength < 21) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const littleEndian = view.getUint8(0) === 1;
  const rawType = view.getUint32(1, littleEndian);
  const type = rawType & 0xff;
  if (type !== 1) return null;
  const coordinateOffset = 5 + ((rawType & 0x20000000) === 0 ? 0 : 4);
  if (bytes.byteLength < coordinateOffset + 16) return null;
  const longitude = view.getFloat64(coordinateOffset, littleEndian);
  const latitude = view.getFloat64(coordinateOffset + 8, littleEndian);
  return Number.isFinite(longitude) && Number.isFinite(latitude)
    && longitude >= -180 && longitude <= 180 && latitude >= -90 && latitude <= 90
    ? [longitude, latitude]
    : null;
}

function locationKey(sourceFile, path) {
  return `${sourceFile ?? ""}\0${path ?? ""}`;
}

function sameSource(left, right) {
  return (left ?? "") === (right ?? "");
}

function rowIdentity(row) {
  return locationKey(row["internal:source_file"], Number(row["internal:current_id"]));
}

function parentIdentity(row) {
  return locationKey(row["internal:source_file"], Number(row["internal:parent_id"]));
}

function contractPath(path) {
  if (typeof path !== "string") return "";
  const slash = path.indexOf("/");
  return slash < 0 ? "" : path.slice(slash + 1);
}

function levelFilename(level) {
  return `${level.replaceAll("/", "__")}.parquet`;
}

function parentMetadataLevel(level) {
  if (level === "children") return "sample";
  const folder = level.slice("children/".length);
  const slash = folder.lastIndexOf("/");
  return slash < 0 ? "children" : `children/${folder.slice(0, slash)}`;
}

function metadataLevelDepth(level) {
  if (level === "sample") return 0;
  return 1 + (level.match(/\//g)?.length ?? 0);
}

function compactObject(values) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null));
}

function orderedMetadataEntries(values) {
  const entries = Object.entries(values);
  const priority = ["id", "dataset_version", "title", "description", "path", "sample_id", "current_id", "parent_id", "source_file", "offset", "size"];
  const first = priority.flatMap((key) => entries.filter(([name]) => name === key));
  const middle = entries.filter(([name]) => !priority.includes(name) && name !== "taco:location");
  const location = entries.filter(([name]) => name === "taco:location");
  return [...first, ...middle, ...location];
}

function metadataMessage(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "metadata-empty";
  paragraph.textContent = text;
  return paragraph;
}

function pointName(point) {
  return String(point.row["fixture:sample_key"] || `sample ${point.row.sample_id ?? "—"}`);
}

function fixtureUrl(fixture) {
  return `${FIXTURE_ROOT}/${String(fixture.path).replace(/^\/+/, "")}`;
}

function topologyLabel(value) {
  return ({ folder: "folder", "single-zip": "zip", "by-size": "cat / size", "by-split": "cat / split", "manual-catalog": "cat / manual" })[value] || value;
}

function formatValue(value) {
  if (value === null || value === undefined) return "—";
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return `binary · ${formatBytes(value.byteLength)}`;
  if (typeof value === "object") {
    return JSON.stringify(value, (_key, item) => typeof item === "bigint" ? item.toString() : item, 2);
  }
  return String(value);
}

function formatLatitude(value) { return `${Math.abs(value).toFixed(5)}° ${value >= 0 ? "N" : "S"}`; }
function formatLongitude(value) { return `${Math.abs(value).toFixed(5)}° ${value >= 0 ? "E" : "W"}`; }

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

function basename(path) {
  const clean = String(path).split(/[?#]/, 1)[0];
  return clean.slice(clean.lastIndexOf("/") + 1);
}

function contentType(path) {
  const extension = basename(path).split(".").pop()?.toLowerCase();
  return ({
    json: "application/json",
    geojson: "application/geo+json",
    tif: "image/tiff",
    tiff: "image/tiff",
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
  })[extension] || "application/octet-stream";
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("Clipboard is unavailable in this browser.");
}

function disableDatasetNavigation(disabled) {
  element.fixtureSelect.disabled = disabled;
  element.datasetMetadata.disabled = disabled || !state.dataset;
}

function setLoading(loading) {
  element.loading.hidden = !loading;
}

function setStatus(kind, text) {
  element.status.className = `status ${kind}`;
  element.status.querySelector("span").textContent = text;
}

function showMessage(text) {
  clearTimeout(state.messageTimer);
  element.message.textContent = text;
  element.message.hidden = false;
  state.messageTimer = setTimeout(() => { element.message.hidden = true; }, 5000);
}

function fail(error) {
  setLoading(false);
  disableDatasetNavigation(false);
  setStatus("error", "Could not read dataset");
  showMessage(messageOf(error));
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error);
}
