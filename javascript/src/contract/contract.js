import { fail } from "../errors.js";
import { deriveLevels, parseStructurePath } from "./structure.js";

/**
 * @typedef {object} TacoContract
 * @property {string[]} structure
 * @property {Record<string, Record<string, any>>} metadata
 * @property {Record<string, any>} derived
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
      validateField(name, declaration, level);
    }
  }

  const derived = collection["taco:derived"] ?? {};
  if (!object(derived)) fail("INVALID_CONTRACT", "taco:derived must be an object");
  return {
    contract: {
      structure: leaves.map((leaf) => leaf.declaration),
      metadata: /** @type {Record<string, Record<string, any>>} */ (metadataObject),
      derived: /** @type {Record<string, any>} */ (derived),
    },
    leaves,
    levels,
  };
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
