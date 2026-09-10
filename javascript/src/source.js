import { openTacoArchive } from "./cozip-profile2.js";
import { parseCollection } from "./contract.js";
import { fail } from "./errors.js";
import { directoryUrl, httpUrl, openHttpObject } from "./http.js";
import { encodeRelativePath, levelFilename, relativePath } from "./paths.js";

const COLLECTION = "COLLECTION.json";
const METADATA = "METADATA";

/**
 * @typedef {ReturnType<typeof parseCollection>} ParsedCollection
 * @typedef {"auto" | "folder" | "zip" | "tacocat"} ContainerHint
 */

class Source {
  /**
   * @param {string} url
   * @param {import("./http.js").HttpClient} client
   * @param {ParsedCollection} parsed
   * @param {"folder" | "zip" | "tacocat"} container
   */
  constructor(url, client, parsed, container) {
    this.url = url;
    this.client = client;
    this.parsed = parsed;
    this.container = container;
  }

  /**
   * @param {string} _level
   * @returns {Promise<{ byteLength: number, slice(start: number, end?: number): Promise<ArrayBuffer> }>}
   */
  async levelBuffer(_level) {
    throw new Error("not implemented");
  }

  /** @param {Record<string, any>} _row @returns {string} */
  location(_row) {
    throw new Error("not implemented");
  }
}

class FolderSource extends Source {
  /** @param {string} url @param {import("./http.js").HttpClient} client @param {ParsedCollection} parsed */
  constructor(url, client, parsed) {
    super(directoryUrl(url), client, parsed, "folder");
  }

  /** @param {string} level */
  async levelBuffer(level) {
    const url = new URL(`${METADATA}/${levelFilename(level)}`, this.url).href;
    return (await openHttpObject(this.client, url)).object;
  }

  /** @param {Record<string, any>} row */
  location(row) {
    const path = relativePath(row["internal:relative_path"], "internal:relative_path");
    return new URL(`DATA/${encodeRelativePath(path)}`, this.url).href;
  }
}

class ZipSource extends Source {
  /**
   * @param {string} url
   * @param {import("./http.js").HttpClient} client
   * @param {ParsedCollection} parsed
   * @param {import("./cozip-profile2.js").TacoArchive} archive
   */
  constructor(url, client, parsed, archive) {
    super(url, client, parsed, "zip");
    this.archive = archive;
  }

  /** @param {string} level */
  async levelBuffer(level) {
    return this.archive.buffer(`${METADATA}/${levelFilename(level)}`);
  }

  /** @param {Record<string, any>} row */
  location(row) {
    const { offset, size } = offsetAndSize(row);
    return `/vsisubfile/${offset}_${size},/vsicurl/${this.url}`;
  }
}

class TacocatSource extends Source {
  /** @param {string} url @param {import("./http.js").HttpClient} client @param {ParsedCollection} parsed */
  constructor(url, client, parsed) {
    const base = directoryUrl(url);
    super(base, client, parsed, "tacocat");
    this.partitionBase = new URL("../", base).href;
  }

  /** @param {string} level */
  async levelBuffer(level) {
    const url = new URL(levelFilename(level), this.url).href;
    return (await openHttpObject(this.client, url)).object;
  }

  /** @param {Record<string, any>} row */
  location(row) {
    const file = relativePath(row["internal:source_file"], "internal:source_file");
    if (!this.parsed.sources?.files.has(file)) {
      fail("UNKNOWN_SOURCE", `metadata references partition not declared by taco:sources: ${file}`);
    }
    const partition = new URL(encodeRelativePath(file), this.partitionBase).href;
    const { offset, size } = offsetAndSize(row);
    return `/vsisubfile/${offset}_${size},/vsicurl/${partition}`;
  }
}

/**
 * @param {string} source
 * @param {import("./http.js").HttpClient} client
 * @param {ContainerHint} hint
 */
export async function openSource(source, client, hint = "auto") {
  const url = httpUrl(source);
  if (!["auto", "folder", "zip", "tacocat"].includes(hint)) {
    throw new TypeError("taco: container must be auto, folder, zip, or tacocat");
  }
  const zip = hint === "zip" || (hint === "auto" && new URL(url).pathname.toLowerCase().endsWith(".zip"));
  if (zip) return openZip(url, client);

  const base = directoryUrl(url);
  const bytes = await client.get(new URL(COLLECTION, base).href);
  const parsed = parseCollectionJson(bytes, new URL(COLLECTION, base).href);
  const actual = parsed.sources ? "tacocat" : "folder";
  if (hint !== "auto" && hint !== actual) {
    fail("CONTAINER_MISMATCH", `requested ${hint} but COLLECTION.json describes ${actual}`);
  }
  return actual === "tacocat"
    ? new TacocatSource(base, client, parsed)
    : new FolderSource(base, client, parsed);
}

/** @param {string} url @param {import("./http.js").HttpClient} client */
async function openZip(url, client) {
  const archive = await openTacoArchive(url, client);
  const parsed = parseCollectionJson(await archive.read(COLLECTION), `${url}#${COLLECTION}`);
  if (parsed.sources) {
    fail("INVALID_COLLECTION", "taco:sources must not appear inside a ZIP partition");
  }
  const expected = new Set([
    COLLECTION,
    ...parsed.levels.map((level) => `${METADATA}/${levelFilename(level)}`),
  ]);
  const missing = [...expected].filter((name) => !archive.entries.has(name));
  const unknown = [...archive.entries.keys()].filter((name) => !expected.has(name));
  if (missing.length || unknown.length) {
    fail(
      "INVALID_PRIORITY_INDEX",
      `TACO priority entries do not match the contract (missing: ${missing.join(", ") || "none"}; unknown: ${unknown.join(", ") || "none"})`,
    );
  }
  return new ZipSource(url, client, parsed, archive);
}

/** @param {Uint8Array} bytes @param {string} source */
function parseCollectionJson(bytes, source) {
  let value;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) {
    fail("INVALID_COLLECTION", `could not parse ${source}: ${error instanceof Error ? error.message : error}`);
  }
  return parseCollection(value);
}

/** @param {Record<string, any>} row */
function offsetAndSize(row) {
  const offset = safeInteger(row["internal:offset"], "internal:offset");
  const size = safeInteger(row["internal:size"], "internal:size");
  if (offset < 0 || size <= 0 || !Number.isSafeInteger(offset + size)) {
    fail("INVALID_OFFSET", `invalid payload range ${offset}_${size}`);
  }
  return { offset, size };
}

/** @param {unknown} value @param {string} context */
export function safeInteger(value, context) {
  if (typeof value === "bigint") {
    if (value < 0n || value > BigInt(Number.MAX_SAFE_INTEGER)) {
      fail("UNSAFE_INTEGER", `${context} exceeds JavaScript's safe integer range`);
    }
    return Number(value);
  }
  if (!Number.isSafeInteger(value)) fail("INVALID_METADATA", `${context} must be an integer`);
  return /** @type {number} */ (value);
}

export { Source };
