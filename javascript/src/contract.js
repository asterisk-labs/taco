import { fail } from "./errors.js";
import { relativePath } from "./paths.js";

export const SUPPORTED_TACO_VERSION = "3.0.0";

/**
 * @typedef {object} TacoLeaf
 * @property {string} declaration
 * @property {string} key
 * @property {boolean} variable
 * @property {RegExp | null} pattern
 *
 * @typedef {object} TacoContract
 * @property {string[] | null} structure
 * @property {Record<string, Record<string, any>>} metadata
 * @property {Record<string, any>} derived
 *
 * @typedef {object} TacoSources
 * @property {Record<string, any>} value
 * @property {Set<string>} files
 *
 * @typedef {object} ParsedCollection
 * @property {Record<string, any>} collection
 * @property {TacoContract} contract
 * @property {TacoLeaf[] | null} leaves
 * @property {string[]} levels
 * @property {TacoSources | null} sources
 */

/** @param {unknown} value */
function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Parse and validate the parts of COLLECTION.json needed by the reader.
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
  for (const name of ["id", "dataset_version", "description"]) {
    if (typeof collection[name] !== "string" || collection[name].length === 0) {
      fail("INVALID_COLLECTION", `${name} must be a non-empty string`);
    }
  }
  for (const name of ["licenses", "providers", "tasks"]) {
    if (!Array.isArray(collection[name]) || collection[name].length === 0) {
      fail("INVALID_COLLECTION", `${name} must be a non-empty list`);
    }
  }
  if (!("taco:structure" in collection) || !("taco:metadata" in collection)) {
    fail("INVALID_CONTRACT", "COLLECTION.json must declare taco:structure and taco:metadata");
  }

  const structureValue = collection["taco:structure"];
  if (structureValue !== null && !Array.isArray(structureValue)) {
    fail("INVALID_CONTRACT", "taco:structure must be a list or null");
  }
  /** @type {TacoLeaf[] | null} */
  const structure =
    structureValue === null
      ? null
      : /** @type {unknown[]} */ (structureValue).map((entry, index) =>
          parseStructurePath(entry, index),
        );
  if (structure && new Set(structure.map((leaf) => leaf.declaration)).size !== structure.length) {
    fail("INVALID_CONTRACT", "taco:structure contains duplicate declarations");
  }

  const levels = deriveLevels(structure);
  const metadata = collection["taco:metadata"];
  if (!object(metadata)) fail("INVALID_CONTRACT", "taco:metadata must be an object");
  const metadataObject = /** @type {Record<string, any>} */ (metadata);
  const expectedLevels = new Set(levels);
  const actualLevels = Object.keys(metadataObject);
  const missing = levels.filter((level) => !(level in metadataObject));
  const unknown = actualLevels.filter((level) => !expectedLevels.has(level));
  if (missing.length || unknown.length) {
    fail(
      "INVALID_CONTRACT",
      `metadata levels do not match the structure (missing: ${missing.join(", ") || "none"}; unknown: ${unknown.join(", ") || "none"})`,
    );
  }
  for (const level of levels) {
    const fields = metadataObject[level];
    if (!object(fields)) fail("INVALID_CONTRACT", `metadata for ${level} must be an object`);
    for (const [name, declaration] of Object.entries(/** @type {Record<string, any>} */ (fields))) {
      validateField(name, declaration, level);
    }
  }

  const derived = collection["taco:derived"] ?? {};
  if (!object(derived)) fail("INVALID_CONTRACT", "taco:derived must be an object");
  const sources = parseSources(collection["taco:sources"]);

  return {
    collection,
    contract: {
      structure: structure === null ? null : structure.map((leaf) => leaf.declaration),
      metadata: /** @type {Record<string, Record<string, any>>} */ (metadataObject),
      derived: /** @type {Record<string, any>} */ (derived),
    },
    leaves: structure,
    levels,
    sources,
  };
}

/**
 * @param {unknown} value
 * @param {number} index
 * @returns {TacoLeaf}
 */
function parseStructurePath(value, index) {
  const declaration = relativePath(value, `taco:structure[${index}]`, { allowGlob: true });
  const slash = declaration.lastIndexOf("/");
  const folder = slash < 0 ? "" : declaration.slice(0, slash + 1);
  const name = slash < 0 ? declaration : declaration.slice(slash + 1);
  const variable = name.match(/^([^*\[\]]+)\*\[(\d+)\s*,\s*(\d+)\]([^*\[\]]*)$/);
  if (!variable) {
    if (/[*\[\]]/.test(name)) {
      fail(
        "INVALID_CONTRACT",
        `malformed variable leaf ${JSON.stringify(declaration)}; expected prefix*[min,max].ext`,
      );
    }
    return { declaration, key: declaration, variable: false, pattern: null };
  }

  const minimum = Number(variable[2]);
  const maximum = Number(variable[3]);
  if (!Number.isSafeInteger(minimum) || !Number.isSafeInteger(maximum) || minimum > maximum || maximum === 0) {
    fail("INVALID_CONTRACT", `invalid bounds in variable leaf ${JSON.stringify(declaration)}`);
  }
  const prefix = `${folder}${variable[1]}`;
  const suffix = variable[4];
  return {
    declaration,
    key: prefix,
    variable: true,
    pattern: new RegExp(`^${escapeRegex(prefix)}(0|[1-9][0-9]*)${escapeRegex(suffix)}$`),
  };
}

/** @param {TacoLeaf[] | null} leaves */
function deriveLevels(leaves) {
  if (leaves === null) return ["sample"];
  /** @type {Map<string, number>} */
  const folders = new Map();
  let order = 0;
  for (const leaf of leaves) {
    const parts = leaf.declaration.split("/");
    parts.pop();
    for (let depth = 1; depth <= parts.length; depth++) {
      const folder = parts.slice(0, depth).join("/");
      if (!folders.has(folder)) folders.set(folder, order++);
    }
  }
  const ordered = [...folders].sort(([left, leftOrder], [right, rightOrder]) => {
    const depth = left.split("/").length - right.split("/").length;
    return depth || leftOrder - rightOrder;
  });
  return ["sample", "children", ...ordered.map(([folder]) => `children/${folder}`)];
}

/**
 * @param {string} name
 * @param {unknown} declaration
 * @param {string} level
 */
function validateField(name, declaration, level) {
  if (!/^[a-z][a-z0-9_]*:[^:/]+$/.test(name) || name.includes("__")) {
    fail("INVALID_CONTRACT", `invalid qualified field ${JSON.stringify(name)} at ${level}`);
  }
  const namespace = name.slice(0, name.indexOf(":"));
  if (namespace === "taco" || namespace === "cozip" || namespace === "internal") {
    fail("INVALID_CONTRACT", `reserved metadata field ${JSON.stringify(name)} at ${level}`);
  }
  if (!object(declaration)) {
    fail("INVALID_CONTRACT", `field ${level}.${name} must contain a declaration object`);
  }
  const spec = /** @type {Record<string, any>} */ (declaration);
  const keys = Object.keys(spec).sort();
  if (keys.join("\0") !== ["description", "nullable", "type"].join("\0")) {
    fail("INVALID_CONTRACT", `field ${level}.${name} must declare type, nullable, and description`);
  }
  if (
    typeof spec.type !== "string" ||
    typeof spec.nullable !== "boolean" ||
    typeof spec.description !== "string"
  ) {
    fail("INVALID_CONTRACT", `field ${level}.${name} has invalid declaration values`);
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

/**
 * @param {TacoLeaf} leaf
 * @param {string} path
 * @returns {number | null}
 */
export function matchLeaf(leaf, path) {
  if (!leaf.variable) return path === leaf.declaration ? 0 : null;
  const match = leaf.pattern?.exec(path);
  return match ? Number(match[1]) : null;
}

/** @param {string} value */
function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
