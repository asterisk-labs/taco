import { relativePath } from "../container/paths.js";
import { fail } from "../errors.js";

/**
 * @typedef {object} TacoLeaf
 * @property {string} declaration
 * @property {string} key
 * @property {boolean} variable
 * @property {RegExp | null} pattern
 */

/**
 * Parse one structure declaration.
 *
 * @param {unknown} value
 * @param {number} index
 * @returns {TacoLeaf}
 */
export function parseStructurePath(value, index) {
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
  if (!Number.isSafeInteger(minimum) || !Number.isSafeInteger(maximum) || minimum < 1 || minimum > maximum) {
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

/** @param {TacoLeaf[]} leaves */
export function deriveLevels(leaves) {
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
