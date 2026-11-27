import { fail } from "../errors.js";
import { relativePath } from "../container/paths.js";
import { isQualifiedField, parseContract } from "./contract.js";

export const SUPPORTED_TACO_VERSION = "3.0.0";

// The keys of COLLECTION.json outside qualified collection metadata.
const COLLECTION_KEYS = new Set([
  "taco:version",
  "id",
  "description",
  "licenses",
  "providers",
  "tasks",
  "title",
  "curators",
  "keywords",
  "extent",
  "taco:structure",
  "taco:metadata",
  "taco:extensions",
  "taco:sources",
]);
const PROVIDER_KEYS = new Set(["name", "roles", "url", "links"]);
const CURATOR_KEYS = new Set(["name", "organization", "email", "role"]);
const EXTENT_KEYS = new Set(["spatial", "temporal"]);
// The native reader's JSON limit (spec 5.5).
const MAX_DEPTH = 256;
const LONE_SURROGATE = /\p{Cs}/u;
// The characters Python's str.strip() removes, so a blank value means the same as in Python.
const BLANK = /^[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/;

/**
 * @typedef {import("./contract.js").TacoContract} TacoContract
 * @typedef {import("./structure.js").TacoLeaf} TacoLeaf
 * @typedef {object} TacoSources
 * @property {Record<string, any>} value
 * @property {Set<string>} files
 *
 * @typedef {object} ParsedCollection
 * @property {Record<string, any>} collection
 * @property {TacoContract} contract
 * @property {TacoLeaf[]} leaves
 * @property {string[]} levels
 * @property {TacoSources | null} sources
 */

/** @param {unknown} value */
function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** @param {unknown} value */
function text(value) {
  return typeof value === "string" && !BLANK.test(value);
}

/**
 * Parse and validate the collection metadata needed by the reader.
 *
 * @param {unknown} value
 * @returns {ParsedCollection}
 */
export function parseCollection(value) {
  if (!object(value)) fail("INVALID_COLLECTION", "COLLECTION.json must contain an object");
  checkJson(value, 0);
  const collection = /** @type {Record<string, any>} */ (value);
  if (collection["taco:version"] !== SUPPORTED_TACO_VERSION) {
    fail(
      "UNSUPPORTED_TACO_VERSION",
      `reader supports TACO ${SUPPORTED_TACO_VERSION}; got ${JSON.stringify(collection["taco:version"])}`,
    );
  }
  const reserved = Object.keys(collection).filter(
    (key) => /^(taco|internal|cozip):/.test(key) && !COLLECTION_KEYS.has(key),
  );
  if (reserved.length) {
    fail("INVALID_COLLECTION", `COLLECTION.json uses unknown reserved keys: ${reserved.join(", ")}`);
  }
  for (const key of Object.keys(collection)) {
    if (COLLECTION_KEYS.has(key) || isQualifiedField(key)) continue;
    fail(
      "INVALID_COLLECTION",
      key.includes(":")
        ? `invalid collection metadata field ${JSON.stringify(key)}`
        : `unknown unqualified field ${JSON.stringify(key)}`,
    );
  }

  for (const name of ["id", "description"]) {
    if (!text(collection[name])) fail("INVALID_COLLECTION", `${name} must be a non-empty string`);
  }
  if (/[/\\:\0]/.test(collection.id)) fail("INVALID_COLLECTION", "id must not contain /, \\, : or NUL");
  if (collection.title != null && typeof collection.title !== "string") {
    fail("INVALID_COLLECTION", "title must be a string");
  }
  stringList(collection.licenses, "licenses");
  stringList(collection.tasks, "tasks", { optional: true });
  stringList(collection.keywords, "keywords", { optional: true, empty: true });
  stringList(collection["taco:extensions"], "taco:extensions", { optional: true, empty: true });
  const extensions = collection["taco:extensions"] ?? [];
  if (new Set(extensions).size !== extensions.length) {
    fail("INVALID_COLLECTION", "taco:extensions must not repeat an identifier");
  }
  checkProviders(collection.providers);
  checkCurators(collection.curators);
  if (collection.extent != null) checkExtent(collection.extent);

  const parsed = parseContract(collection);
  return {
    collection,
    contract: parsed.contract,
    leaves: parsed.leaves,
    levels: parsed.levels,
    sources: parseSources(collection["taco:sources"]),
  };
}

/**
 * Reject what the native reader's JSON parser rejects and JSON.parse accepts:
 * deeper nesting than 256 levels, numbers beyond a finite double and lone
 * surrogates. Repeated keys are not visible after JSON.parse.
 *
 * @param {unknown} value
 * @param {number} depth
 */
function checkJson(value, depth) {
  if (depth > MAX_DEPTH) fail("INVALID_COLLECTION", `COLLECTION.json nests deeper than ${MAX_DEPTH} levels`);
  if (typeof value === "number" && !Number.isFinite(value)) {
    fail("INVALID_COLLECTION", "COLLECTION.json contains a number that is not finite");
  }
  if (typeof value === "string" && LONE_SURROGATE.test(value)) {
    fail("INVALID_COLLECTION", "COLLECTION.json contains a lone surrogate");
  }
  if (value === null || typeof value !== "object") return;
  for (const [key, item] of Object.entries(value)) {
    checkJson(key, depth);
    checkJson(item, depth + 1);
  }
}

/**
 * @param {unknown} value
 * @param {string} name
 * @param {{ optional?: boolean, empty?: boolean }} [rules]
 */
function stringList(value, name, { optional = false, empty = false } = {}) {
  if (optional && value == null) return;
  if (!Array.isArray(value) || (!empty && value.length === 0) || !value.every(text)) {
    fail("INVALID_COLLECTION", `${name} must be a ${empty ? "" : "non-empty "}list of non-empty strings`);
  }
}

/** @param {unknown} value */
function checkProviders(value) {
  if (!Array.isArray(value) || value.length === 0) fail("INVALID_COLLECTION", "providers must be a non-empty list");
  for (const [index, provider] of value.entries()) {
    const context = `providers[${index}]`;
    checkKeys(provider, PROVIDER_KEYS, context);
    if (!text(provider.name)) fail("INVALID_COLLECTION", `${context}.name must be a non-empty string`);
    stringList(provider.roles, `${context}.roles`, { optional: true, empty: true });
    if (provider.url != null && !(typeof provider.url === "string" && /^https?:\/\//.test(provider.url))) {
      fail("INVALID_COLLECTION", `${context}.url must start with http:// or https://`);
    }
    if (provider.links != null && !(Array.isArray(provider.links) && provider.links.every(object))) {
      fail("INVALID_COLLECTION", `${context}.links must be a list of objects`);
    }
  }
}

/** @param {unknown} value */
function checkCurators(value) {
  if (value == null) return;
  if (!Array.isArray(value)) fail("INVALID_COLLECTION", "curators must be a list of objects");
  for (const [index, curator] of value.entries()) {
    const context = `curators[${index}]`;
    checkKeys(curator, CURATOR_KEYS, context);
    for (const key of CURATOR_KEYS) {
      if (curator[key] != null && typeof curator[key] !== "string") {
        fail("INVALID_COLLECTION", `${context}.${key} must be a string`);
      }
    }
    if (!curator.name && !curator.organization) {
      fail("INVALID_COLLECTION", `${context} needs a name or an organization`);
    }
    if (curator.email != null && !curator.email.includes("@")) {
      fail("INVALID_COLLECTION", `${context}.email must contain @`);
    }
  }
}

/** @param {unknown} value @param {Set<string>} keys @param {string} context */
function checkKeys(value, keys, context) {
  if (!object(value)) fail("INVALID_COLLECTION", `${context} must be an object`);
  const unknown = Object.keys(/** @type {object} */ (value)).filter((key) => !keys.has(key));
  if (unknown.length) fail("INVALID_COLLECTION", `${context} has unknown fields: ${unknown.join(", ")}`);
}

/**
 * Check the shape and bounds of an extent. Its timestamps are not parsed.
 *
 * @param {unknown} value
 */
function checkExtent(value) {
  checkKeys(value, EXTENT_KEYS, "extent");
  const extent = /** @type {Record<string, any>} */ (value);
  const spatial = extent.spatial;
  if (!Array.isArray(spatial) || spatial.length !== 4 || !spatial.every((item) => typeof item === "number")) {
    fail("INVALID_COLLECTION", "extent.spatial must be [west, south, east, north]");
  }
  const [west, south, east, north] = spatial;
  if (!(Math.abs(west) <= 180 && Math.abs(east) <= 180 && -90 <= south && south <= north && north <= 90)) {
    fail("INVALID_COLLECTION", "extent.spatial is outside EPSG:4326 bounds");
  }
  const temporal = extent.temporal;
  const pair = Array.isArray(temporal) && temporal.length === 2;
  if (temporal != null && !(pair && temporal.every((item) => typeof item === "string"))) {
    fail("INVALID_COLLECTION", "extent.temporal must be null or [start, end]");
  }
}

/** @param {unknown} value @returns {TacoSources | null} */
function parseSources(value) {
  if (value === undefined) return null;
  if (!object(value)) fail("INVALID_COLLECTION", "taco:sources must be an object");
  const sources = /** @type {Record<string, any>} */ (value);
  if (!Number.isSafeInteger(sources.samples) || sources.samples < 0) {
    fail("INVALID_COLLECTION", "taco:sources.samples must be a non-negative integer");
  }
  if (!Array.isArray(sources.partitions) || sources.partitions.length === 0) {
    fail("INVALID_COLLECTION", "taco:sources.partitions must be a non-empty list");
  }
  const files = sources.partitions.map((partition, index) => {
    if (!object(partition)) {
      fail("INVALID_COLLECTION", `taco:sources.partitions[${index}] must be an object`);
    }
    const file = relativePath(
      /** @type {Record<string, any>} */ (partition).file,
      `taco:sources.partitions[${index}].file`,
    );
    if (!file.endsWith(".zip")) {
      fail("INVALID_COLLECTION", `taco:sources.partitions[${index}].file must name a .zip partition`);
    }
    const samples = /** @type {Record<string, any>} */ (partition).samples;
    if (!Number.isSafeInteger(samples) || samples < 0) {
      fail("INVALID_COLLECTION", `taco:sources.partitions[${index}].samples is invalid`);
    }
    return file;
  });
  if (new Set(files).size !== files.length) {
    fail("INVALID_COLLECTION", "taco:sources contains duplicate partition files");
  }
  return { value: sources, files: new Set(files) };
}
