import { fail } from "../errors.js";
import { rumiFileField } from "./rumi.js";
import { deriveLevels, parseStructurePath } from "./structure.js";

/**
 * @typedef {object} TacoContract
 * @property {string[]} structure
 * @property {Record<string, Record<string, any>>} metadata
 *
 * @typedef {import("./structure.js").TacoLeaf} TacoLeaf
 */

/** @param {unknown} value */
function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Validate and construct a collection's shared sample contract.
 *
 * @param {Record<string, any>} collection
 * @returns {{contract: TacoContract, leaves: TacoLeaf[], levels: string[]}}
 */
export function parseContract(collection) {
  if (!("taco:structure" in collection) || !("taco:metadata" in collection)) {
    fail("INVALID_CONTRACT", "COLLECTION.json must declare taco:structure and taco:metadata");
  }

  const structureValue = collection["taco:structure"];
  if (!Array.isArray(structureValue) || structureValue.length === 0) {
    fail("INVALID_CONTRACT", "taco:structure must be a non-empty list");
  }
  const leaves = /** @type {unknown[]} */ (structureValue).map((entry, index) => parseStructurePath(entry, index));
  if (new Set(leaves.map((leaf) => leaf.declaration)).size !== leaves.length) {
    fail("INVALID_CONTRACT", "taco:structure contains duplicate declarations");
  }

  const levels = deriveLevels(leaves);
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
      validateField(name, declaration, level, leaves);
    }
  }

  return {
    contract: {
      structure: leaves.map((leaf) => leaf.declaration),
      metadata: /** @type {Record<string, Record<string, any>>} */ (metadataObject),
    },
    leaves,
    levels,
  };
}

/**
 * @param {string} name
 * @param {unknown} declaration
 * @param {string} level
 * @param {TacoLeaf[]} leaves
 */
function validateField(name, declaration, level, leaves) {
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
  const required = ["description", "nullable", "type"];
  if (keys.filter((key) => key !== "files").join("\0") !== required.join("\0")) {
    fail("INVALID_CONTRACT", `field ${level}.${name} must declare type, nullable, and description`);
  }
  if (
    typeof spec.type !== "string" ||
    typeof spec.nullable !== "boolean" ||
    typeof spec.description !== "string"
  ) {
    fail("INVALID_CONTRACT", `field ${level}.${name} has invalid declaration values`);
  }
  if (namespace === "rumi") {
    const suffix = rumiFileField(name);
    if (suffix === null) fail("INVALID_CONTRACT", `invalid Rumi field ${JSON.stringify(name)} at ${level}`);
    const expected = suffix === "header" ? "binary" : "double";
    if (spec.type !== expected) {
      fail("INVALID_CONTRACT", `field ${level}.${name} must have type ${expected}, got ${spec.type}`);
    }
    if ("files" in spec) validateRumiFiles(spec.files, suffix, name, level, leaves);
  } else if ("files" in spec) {
    fail("INVALID_CONTRACT", `field ${level}.${name} cannot be restricted to files`);
  }
}

/**
 * @param {unknown} value
 * @param {string} suffix
 * @param {string} field
 * @param {string} level
 * @param {TacoLeaf[]} leaves
 */
function validateRumiFiles(value, suffix, field, level, leaves) {
  if (suffix === "header") {
    fail("INVALID_CONTRACT", `field ${level}.${field} cannot be restricted to files`);
  }
  if (!Array.isArray(value) || value.length === 0) {
    fail("INVALID_CONTRACT", `files for ${level}.${field} must be a non-empty list of structure declarations`);
  }
  if (value.some((file) => typeof file !== "string")) {
    fail("INVALID_CONTRACT", `files for ${level}.${field} must contain structure declarations`);
  }
  if (new Set(value).size !== value.length) {
    fail("INVALID_CONTRACT", `files for ${level}.${field} must not contain duplicates`);
  }
  const byDeclaration = new Map(leaves.map((leaf) => [leaf.declaration, leaf]));
  for (const file of value) {
    const leaf = byDeclaration.get(file);
    if (!leaf) fail("INVALID_CONTRACT", `field ${level}.${field} names unknown file ${JSON.stringify(file)}`);
    const slash = file.lastIndexOf("/");
    const fileLevel = slash < 0 ? "children" : `children/${file.slice(0, slash)}`;
    if (fileLevel !== level) {
      fail("INVALID_CONTRACT", `field ${level}.${field} names a file outside that level: ${JSON.stringify(file)}`);
    }
  }
}
