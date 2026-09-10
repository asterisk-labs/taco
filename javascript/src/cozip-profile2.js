import { fail } from "./errors.js";
import { HttpObject, openHttpObject } from "./http.js";

const LFH_SIZE = 51;
const INDEX_HEADER_SIZE = 11;
const HASH_WINDOW_SIZE = 32768;
const MIN_ARCHIVE_SIZE = LFH_SIZE + HASH_WINDOW_SIZE;
const BOOTSTRAP_SIZE = 65536;
const LFH_MAGIC = 0x04034b50;
const INDEX_MAGIC = 0x50495a43;
const EXTRA_HEADER_ID = 0xca0c;
const FORBIDDEN_FLAGS = 0x2049;
const COZIP_NAME = "__cozip__";
const FORMAT_VERSION = 1;
const PROFILE_TACO = 2;
const FNV_OFFSET_BASIS = 0xcbf29ce484222325n;
const FNV_PRIME = 0x100000001b3n;
const UINT64_MASK = 0xffffffffffffffffn;

/** @typedef {{ offset: number, size: number }} PriorityEntry */

export class TacoArchive {
  /**
   * @param {string} url
   * @param {HttpObject} object
   * @param {Map<string, PriorityEntry>} entries
   */
  constructor(url, object, entries) {
    this.url = url;
    this.byteLength = object.byteLength;
    this.entries = entries;
    this.object = object;
  }

  /** @param {string} name */
  entry(name) {
    const entry = this.entries.get(name);
    if (!entry) fail("MISSING_ENTRY", `TACO priority entry ${JSON.stringify(name)} is missing`);
    return entry;
  }

  /** @param {string} name */
  async read(name) {
    const entry = this.entry(name);
    return this.object.sliceBytes(entry.offset, entry.offset + entry.size);
  }

  /** @param {string} name */
  buffer(name) {
    const entry = this.entry(name);
    return this.object.subBuffer(entry.offset, entry.size);
  }
}

/**
 * Open and authenticate a CoZIP TACO-profile archive.
 *
 * This implementation is private to @asterisk-labs/taco. It intentionally
 * does not import or depend on @asterisk-labs/cozip.
 *
 * @param {string} url
 * @param {import("./http.js").HttpClient} client
 * @returns {Promise<TacoArchive>}
 */
export async function openTacoArchive(url, client) {
  const { object, initialBytes } = await openHttpObject(client, url, BOOTSTRAP_SIZE - 1);
  const archiveSize = object.byteLength;
  if (archiveSize < MIN_ARCHIVE_SIZE) {
    fail(
      "ARCHIVE_TOO_SMALL",
      `archive is ${archiveSize} bytes; minimum is ${MIN_ARCHIVE_SIZE}`,
    );
  }

  const indexSize = readIndexPayloadSize(initialBytes);
  const indexEnd = LFH_SIZE + indexSize;
  if (indexEnd > archiveSize) {
    fail("TRUNCATED_INDEX", "index payload extends beyond the archive");
  }
  const indexBytes =
    indexEnd <= initialBytes.length
      ? initialBytes.subarray(0, indexEnd)
      : concat(initialBytes, await object.sliceBytes(initialBytes.length, indexEnd));
  const entries = parseIndex(indexBytes, archiveSize);
  await verifyIntegrityHash(object, indexBytes, indexSize);
  return new TacoArchive(url, object, entries);
}

/** @param {Uint8Array} bytes */
function readIndexPayloadSize(bytes) {
  if (bytes.length < LFH_SIZE) {
    fail("TRUNCATED_INDEX", "response is shorter than the 51-byte index header");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== LFH_MAGIC) {
    fail("INVALID_INDEX", "byte 0 is not a ZIP local file header");
  }
  if ((view.getUint16(6, true) & FORBIDDEN_FLAGS) !== 0) {
    fail("INVALID_INDEX", "index local file header has forbidden ZIP flags");
  }
  if (view.getUint16(8, true) !== 0) {
    fail("INVALID_INDEX", "index entry must use STORE compression");
  }
  if (view.getUint16(26, true) !== 9 || view.getUint16(28, true) !== 12) {
    fail("INVALID_INDEX", "first local file header does not match the CoZIP layout");
  }
  const size = view.getUint32(18, true);
  if (size === 0 || size === 0xffffffff || view.getUint32(22, true) !== size) {
    fail("INVALID_INDEX", "index entry has invalid size fields");
  }
  if (size < INDEX_HEADER_SIZE) {
    fail("TRUNCATED_INDEX", "index payload is shorter than its 11-byte header");
  }
  return size;
}

/**
 * @param {Uint8Array} bytes
 * @param {number} archiveSize
 * @returns {Map<string, PriorityEntry>}
 */
function parseIndex(bytes, archiveSize) {
  if (bytes.length < LFH_SIZE + INDEX_HEADER_SIZE) {
    fail("TRUNCATED_INDEX", "index payload is truncated");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decoder = new TextDecoder();
  if (decoder.decode(bytes.subarray(30, 39)) !== COZIP_NAME) {
    fail("INVALID_INDEX", "first ZIP entry is not __cozip__");
  }
  if (view.getUint16(39, true) !== EXTRA_HEADER_ID || view.getUint16(41, true) !== 8) {
    fail("INVALID_INDEX", "the 8-byte CoZIP integrity field is missing");
  }
  if (view.getUint32(LFH_SIZE, true) !== INDEX_MAGIC) {
    fail("INVALID_INDEX", "index magic is not CZIP");
  }
  const version = view.getUint16(LFH_SIZE + 4, true);
  if (version !== FORMAT_VERSION) {
    fail("UNSUPPORTED_COZIP_VERSION", `TACO requires CoZIP binary version 1; got ${version}`);
  }
  const profile = view.getUint8(LFH_SIZE + 6);
  if (profile !== PROFILE_TACO) {
    fail("UNKNOWN_PROFILE", `TACO requires CoZIP profile 2; got profile ${profile}`);
  }

  const count = view.getUint32(LFH_SIZE + 7, true);
  const payloadSize = view.getUint32(18, true);
  const payloadEnd = LFH_SIZE + payloadSize;
  if (payloadEnd > bytes.length) {
    fail("TRUNCATED_INDEX", "index payload extends beyond the bootstrap bytes");
  }
  if (count > Math.floor((payloadSize - INDEX_HEADER_SIZE) / 18)) {
    fail("INVALID_INDEX", "index entry count does not fit in its payload");
  }

  let cursor = LFH_SIZE + INDEX_HEADER_SIZE;
  const nameLengths = new Array(count);
  let namesSize = 0;
  for (let index = 0; index < count; index++) {
    nameLengths[index] = view.getUint16(cursor, true);
    namesSize += nameLengths[index];
    cursor += 2;
  }
  if (INDEX_HEADER_SIZE + count * 18 + namesSize !== payloadSize) {
    fail("INVALID_INDEX", "index sections do not match the declared payload size");
  }

  const names = new Array(count);
  const uniqueNames = new Set();
  for (let index = 0; index < count; index++) {
    const nameBytes = bytes.subarray(cursor, cursor + nameLengths[index]);
    const name = decoder.decode(nameBytes);
    validateArchiveName(nameBytes, name, index);
    if (uniqueNames.has(name)) {
      fail("INVALID_INDEX", `index contains duplicate name ${JSON.stringify(name)}`);
    }
    uniqueNames.add(name);
    names[index] = name;
    cursor += nameLengths[index];
  }

  const offsets = new Array(count);
  for (let index = 0; index < count; index++) {
    offsets[index] = readSafeUint64(view, cursor, `offset for ${JSON.stringify(names[index])}`);
    cursor += 8;
  }

  const sizes = new Array(count);
  for (let index = 0; index < count; index++) {
    const size = readSafeUint64(view, cursor, `size for ${JSON.stringify(names[index])}`);
    const end = offsets[index] + size;
    if (size === 0) {
      fail("INVALID_OFFSET", `priority entry ${JSON.stringify(names[index])} is empty`);
    }
    if (!Number.isSafeInteger(end) || end > archiveSize) {
      fail("INVALID_OFFSET", `priority entry ${JSON.stringify(names[index])} exceeds the archive`);
    }
    sizes[index] = size;
    cursor += 8;
  }

  const entries = new Map();
  for (let index = 0; index < count; index++) {
    entries.set(names[index], { offset: offsets[index], size: sizes[index] });
  }
  return entries;
}

/**
 * @param {Uint8Array} bytes
 * @param {string} name
 * @param {number} index
 */
function validateArchiveName(bytes, name, index) {
  if (bytes.length === 0) {
    fail("INVALID_INDEX", `priority entry ${index} has an empty name`);
  }
  if (bytes.some((byte) => byte === 0 || byte >= 0x80)) {
    fail("INVALID_INDEX", `priority entry ${index} name is not non-empty ASCII`);
  }
  if (
    name.startsWith("/") ||
    /^[A-Za-z]:/.test(name) ||
    name.endsWith("/") ||
    name.includes("\\") ||
    name.split("/").some((part) => part === "." || part === "..")
  ) {
    fail("INVALID_INDEX", `priority entry ${index} has invalid name ${JSON.stringify(name)}`);
  }
  if (name === COZIP_NAME || name === "__cozip_padding__") {
    fail("INVALID_INDEX", `index must not list reserved entry ${JSON.stringify(name)}`);
  }
}

/**
 * @param {DataView} view
 * @param {number} offset
 * @param {string} context
 */
function readSafeUint64(view, offset, context) {
  const value = view.getBigUint64(offset, true);
  if (value > BigInt(Number.MAX_SAFE_INTEGER)) {
    fail("UNSAFE_INTEGER", `${context} exceeds JavaScript's safe integer range`);
  }
  return Number(value);
}

/**
 * @param {HttpObject} object
 * @param {Uint8Array} head
 * @param {number} indexSize
 */
async function verifyIntegrityHash(object, head, indexSize) {
  const view = new DataView(head.buffer, head.byteOffset, head.byteLength);
  const stored = view.getBigUint64(43, true);
  const indexEnd = LFH_SIZE + indexSize;
  let hash = fnv1a64(head.subarray(LFH_SIZE, indexEnd));
  const suffixStart = object.byteLength - HASH_WINDOW_SIZE;
  const suffix = await object.sliceBytes(suffixStart, object.byteLength);
  const remainingStart = Math.max(suffixStart, indexEnd);
  if (remainingStart < object.byteLength) {
    hash = fnv1a64(suffix.subarray(remainingStart - suffixStart), hash);
  }
  if (hash !== stored) {
    fail("HASH_MISMATCH", "integrity hash does not match the index and archive suffix");
  }
}

/**
 * @param {Uint8Array} bytes
 * @param {bigint} [hash]
 */
function fnv1a64(bytes, hash = FNV_OFFSET_BASIS) {
  for (const byte of bytes) {
    hash ^= BigInt(byte);
    hash = (hash * FNV_PRIME) & UINT64_MASK;
  }
  return hash;
}

/** @param {Uint8Array} left @param {Uint8Array} right */
function concat(left, right) {
  const result = new Uint8Array(left.length + right.length);
  result.set(left, 0);
  result.set(right, left.length);
  return result;
}
