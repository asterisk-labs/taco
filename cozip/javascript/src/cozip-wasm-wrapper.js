const PROFILE_NONE = 0;

function defaultFactory() {
  if (typeof globalThis.createCozipModule !== "function") {
    throw new Error("createCozipModule is not loaded. Include cozip/javascript/wasm/cozip.js first.");
  }
  return globalThis.createCozipModule;
}

async function bytesFrom(source) {
  if (source instanceof Uint8Array) {
    return source;
  }
  if (source instanceof ArrayBuffer) {
    return new Uint8Array(source);
  }
  if (source && typeof source.arrayBuffer === "function") {
    return new Uint8Array(await source.arrayBuffer());
  }
  throw new TypeError("entry data must be a Uint8Array, ArrayBuffer, Blob, or File");
}

function malloc(module, size) {
  const ptr = module._malloc(size);
  if (!ptr) {
    throw new Error(`WASM malloc failed for ${size} bytes`);
  }
  return ptr;
}

function writeCString(module, value) {
  const size = module.lengthBytesUTF8(value) + 1;
  const ptr = malloc(module, size);
  module.stringToUTF8(value, ptr, size);
  return ptr;
}

function writeBytes(module, bytes) {
  const ptr = malloc(module, bytes.length);
  module.HEAPU8.set(bytes, ptr);
  return ptr;
}

function readError(module, errPtr, messageOffset) {
  const message = module.UTF8ToString(errPtr + messageOffset);
  return message || "cozip WASM call failed";
}

function unlinkIfExists(module, path) {
  try {
    module.FS.unlink(path);
  } catch (_error) {
    // MEMFS reports missing files as exceptions.
  }
}

export async function createCozipWasmWriter(options = {}) {
  const factory = options.factory || defaultFactory();
  const module = await factory({
    locateFile: options.locateFile,
    ...options.moduleOptions,
  });

  if (!module.FS) {
    throw new Error("cozip WASM was built without exported FS support");
  }

  const errorSize = module._cozip_wasm_error_size();
  const errorMessageOffset = module._cozip_wasm_error_message_offset();

  async function writeArchive({ entries, profile = PROFILE_NONE, outputPath = "/out.cozip.zip" }) {
    if (!Array.isArray(entries) || entries.length === 0) {
      throw new Error("writeArchive requires at least one entry");
    }

    const normalized = [];
    for (const entry of entries) {
      if (!entry || !entry.name) {
        throw new Error("each entry needs a non-empty name");
      }
      const data = await bytesFrom(entry.data ?? entry.file ?? entry.source);
      if (data.length === 0) {
        throw new Error(`entry ${entry.name} has an empty payload`);
      }
      normalized.push({
        name: String(entry.name),
        data,
        inIndex: entry.inIndex !== false,
      });
    }

    const allocated = [];
    const n = normalized.length;
    const namesPtr = malloc(module, n * 4);
    const buffersPtr = malloc(module, n * 4);
    const sizesPtr = malloc(module, n * 4);
    const inIndexPtr = malloc(module, n);
    const outputPathPtr = writeCString(module, outputPath);
    const errPtr = malloc(module, errorSize);
    allocated.push(namesPtr, buffersPtr, sizesPtr, inIndexPtr, outputPathPtr, errPtr);

    try {
      for (let i = 0; i < n; i += 1) {
        const namePtr = writeCString(module, normalized[i].name);
        const dataPtr = writeBytes(module, normalized[i].data);
        allocated.push(namePtr, dataPtr);

        module.HEAPU32[(namesPtr >> 2) + i] = namePtr;
        module.HEAPU32[(buffersPtr >> 2) + i] = dataPtr;
        module.HEAPU32[(sizesPtr >> 2) + i] = normalized[i].data.length;
        module.HEAPU8[inIndexPtr + i] = normalized[i].inIndex ? 1 : 0;
      }

      unlinkIfExists(module, outputPath);

      const status = module._cozip_wasm_write_archive_from_buffers(
        outputPathPtr,
        namesPtr,
        buffersPtr,
        sizesPtr,
        inIndexPtr,
        n,
        profile,
        errPtr,
      );

      if (status !== 0) {
        throw new Error(readError(module, errPtr, errorMessageOffset));
      }

      const out = module.FS.readFile(outputPath);
      unlinkIfExists(module, outputPath);
      return new Uint8Array(out);
    } finally {
      for (let i = allocated.length - 1; i >= 0; i -= 1) {
        module._free(allocated[i]);
      }
    }
  }

  return {
    module,
    writeArchive,
  };
}

export { PROFILE_NONE };
