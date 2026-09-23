import { fail } from "../errors.js";
import { relativePath } from "../container/paths.js";
import { parseContract } from "./contract.js";

export const SUPPORTED_TACO_VERSION = "3.0.0";

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

/**
 * Parse and validate the collection metadata needed by the reader.
 *
 * @param {unknown} value
 * @returns {ParsedCollection}
 */
export function parseCollection(value) {
  if (!object(value)) fail("INVALID_COLLECTION", "COLLECTION.json must contain an object");
  const collection = /** @type {Record<string, any>} */ (value);
  if (collection["taco:version"] !== SUPPORTED_TACO_VERSION) {
    fail(
      "UNSUPPORTED_TACO_VERSION",
      `reader supports TACO ${SUPPORTED_TACO_VERSION}; got ${JSON.stringify(collection["taco:version"])}`,
    );
  }
  for (const name of ["id", "description"]) {
    if (typeof collection[name] !== "string" || collection[name].length === 0) {
      fail("INVALID_COLLECTION", `${name} must be a non-empty string`);
    }
  }
  for (const name of ["licenses", "providers", "tasks"]) {
    if (name === "tasks" && collection.tasks === undefined) continue;
    if (!Array.isArray(collection[name]) || collection[name].length === 0) {
      fail("INVALID_COLLECTION", `${name} must be a non-empty list`);
    }
  }

  const parsed = parseContract(collection);
  return {
    collection,
    contract: parsed.contract,
    leaves: parsed.leaves,
    levels: parsed.levels,
    sources: parseSources(collection["taco:sources"]),
  };
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
