import { levelFilename, relativePath } from "../container/paths.js";
import { fail } from "../errors.js";

/**
 * @typedef {object} TacoLeaf
 * @property {string} declaration
 * @property {string} key
 * @property {boolean} variable
 * @property {string} suffix The text after the index of a sequence, "" for a fixed file.
 * @property {number} maximum The number of files a sequence allows, 1 for a fixed file.
 *
 * @typedef {object} Sequence
 * @property {string} prefix
 * @property {string} suffix
 * @property {number} maximum
 */

// prefix*[a,b]suffix, with decimal bounds that have no sign, space or leading zero.
const VARIABLE_LEAF = /^([^*\[\]]+)\*\[(0|[1-9][0-9]*),(0|[1-9][0-9]*)\]([^*\[\]]*)$/;

/**
 * Parse one structure declaration.
 *
 * @param {unknown} value
 * @param {number} index
 * @returns {TacoLeaf}
 */
export function parseStructurePath(value, index) {
  // The prefix and suffix hold no reserved character, so the names a sequence
  // produces are valid exactly when the declaration is.
  const declaration = relativePath(value, `taco:structure[${index}]`, { allowGlob: true });
  const slash = declaration.lastIndexOf("/");
  const folder = slash < 0 ? "" : declaration.slice(0, slash + 1);
  const name = slash < 0 ? declaration : declaration.slice(slash + 1);
  const variable = name.match(VARIABLE_LEAF);
  if (!variable) {
    if (/[*\[\]]/.test(name)) {
      fail(
        "INVALID_CONTRACT",
        `malformed variable leaf ${JSON.stringify(declaration)}; expected prefix*[min,max].ext`,
      );
    }
    return { declaration, key: declaration, variable: false, suffix: "", maximum: 1 };
  }

  const minimum = Number(variable[2]);
  const maximum = Number(variable[3]);
  if (!Number.isSafeInteger(minimum) || !Number.isSafeInteger(maximum) || minimum < 1 || minimum > maximum) {
    fail("INVALID_CONTRACT", `invalid bounds in variable leaf ${JSON.stringify(declaration)}`);
  }
  return { declaration, key: `${folder}${variable[1]}`, variable: true, suffix: variable[4], maximum };
}

/**
 * Reject entries of a folder that readers and case-insensitive file systems
 * cannot tell apart (spec 5.2): repeated identifiers, identifiers that differ
 * only in case, and names that a variable sequence can also produce.
 *
 * @param {TacoLeaf[]} leaves
 */
export function checkStructure(leaves) {
  /** @type {Map<string, Array<{ name: string, leaf: TacoLeaf | null }>>} */
  const folders = new Map([["", []]]);
  for (const leaf of leaves) {
    const parts = leaf.declaration.split("/");
    const name = /** @type {string} */ (parts.pop());
    let folder = "";
    for (const part of parts) {
      const entries = folders.get(folder) ?? [];
      if (!entries.some((entry) => entry.leaf === null && entry.name === part)) {
        entries.push({ name: part, leaf: null });
      }
      folder = folder ? `${folder}/${part}` : part;
      if (!folders.has(folder)) folders.set(folder, []);
    }
    // A sequence is identified by its prefix.
    folders.get(folder)?.push({ name: leaf.variable ? sequence(leaf).prefix : name, leaf });
  }

  for (const [folder, entries] of folders) {
    const where = folder || "the sample root";
    const names = entries.map((entry) => entry.name);
    if (new Set(names).size !== names.length) {
      fail("INVALID_CONTRACT", `children under ${where} must have distinct identifiers`);
    }
    /** @type {Map<string, string>} */
    const seen = new Map();
    for (const name of names) {
      const other = seen.get(name.toLowerCase());
      if (other !== undefined) {
        fail(
          "INVALID_CONTRACT",
          `${JSON.stringify(other)} and ${JSON.stringify(name)} under ${where} differ only in case`,
        );
      }
      seen.set(name.toLowerCase(), name);
    }

    const sequences = entries.flatMap(({ leaf }) => (leaf?.variable ? [leaf] : []));
    const lowered = sequences.map((leaf) => lower(sequence(leaf)));
    /** @param {string} name @param {TacoLeaf} other @returns {never} */
    const overlap = (name, other) => fail(
      "INVALID_CONTRACT",
      `${JSON.stringify(name)} overlaps ${JSON.stringify(other.declaration)} under ${where}`,
    );
    for (const { name, leaf } of entries) {
      if (leaf?.variable) continue;
      const other = lowered.findIndex((item) => sequenceIndex(item, name.toLowerCase()) !== null);
      if (other >= 0) overlap(name, sequences[other]);
    }
    for (let first = 0; first < sequences.length; first++) {
      for (let second = first + 1; second < sequences.length; second++) {
        if (sequencesOverlap(lowered[first], lowered[second])) overlap(sequences[first].declaration, sequences[second]);
      }
    }
  }
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
  const levels = ["sample", "children", ...ordered.map(([folder]) => `children/${folder}`)];

  // 'a_/b' and 'a/_b' share a file name, and some file systems ignore case.
  /** @type {Map<string, string>} */
  const files = new Map();
  for (const level of levels) {
    const file = levelFilename(level);
    const other = files.get(file.toLowerCase());
    if (other !== undefined) {
      const note = levelFilename(other) === file ? "" : " on a case-insensitive file system";
      fail(
        "INVALID_CONTRACT",
        `levels ${JSON.stringify(other)} and ${JSON.stringify(level)} both map to METADATA/${file}${note}`,
      );
    }
    files.set(file.toLowerCase(), level);
  }
  return levels;
}

/**
 * @param {TacoLeaf} leaf
 * @param {string} path
 * @returns {number | null}
 */
export function matchLeaf(leaf, path) {
  if (!leaf.variable) return path === leaf.declaration ? 0 : null;
  return sequenceIndex({ prefix: leaf.key, suffix: leaf.suffix, maximum: leaf.maximum }, path);
}

/**
 * The index a sequence gives a name: decimal digits without a leading zero
 * between its prefix and suffix, below its maximum.
 *
 * @param {Sequence} sequence
 * @param {string} name
 * @returns {number | null}
 */
function sequenceIndex({ prefix, suffix, maximum }, name) {
  if (name.length <= prefix.length + suffix.length || !name.startsWith(prefix) || !name.endsWith(suffix)) {
    return null;
  }
  const digits = name.slice(prefix.length, name.length - suffix.length);
  if (!/^(0|[1-9][0-9]*)$/.test(digits)) return null;
  const index = Number(digits);
  return index < maximum ? index : null;
}

/**
 * Whether two sequences can produce the same name. For each length, the
 * digits that both indexes share take their smallest allowed value, because
 * larger digits can only exceed a maximum.
 *
 * @param {Sequence} first
 * @param {Sequence} second
 */
function sequencesOverlap(first, second) {
  /** @param {Sequence} item */
  const fixed = (item) => item.prefix.length + item.suffix.length;
  /** @param {Sequence} item */
  const longest = (item) => fixed(item) + String(item.maximum - 1).length;
  const end = Math.min(longest(first), longest(second));
  for (let length = Math.max(fixed(first), fixed(second)) + 1; length <= end; length++) {
    let name = "";
    for (let position = 0; position < length; position++) {
      const left = fixedCharacter(first, length, position);
      const right = fixedCharacter(second, length, position);
      if (left !== null && right !== null && left !== right) break;
      const leading = startsIndex(first, length, position) || startsIndex(second, length, position);
      name += left ?? right ?? (leading ? "1" : "0");
    }
    if (name.length === length && sequenceIndex(first, name) !== null && sequenceIndex(second, name) !== null) {
      return true;
    }
  }
  return false;
}

/**
 * The character a sequence fixes at a position of a name of this length, or
 * null inside its index.
 *
 * @param {Sequence} item
 * @param {number} length
 * @param {number} position
 */
function fixedCharacter({ prefix, suffix }, length, position) {
  if (position < prefix.length) return prefix[position];
  if (position >= length - suffix.length) return suffix[position - length + suffix.length];
  return null;
}

/**
 * Whether an index of two or more digits, which cannot start with 0, starts
 * at this position.
 *
 * @param {Sequence} item
 * @param {number} length
 * @param {number} position
 */
function startsIndex({ prefix, suffix }, length, position) {
  return position === prefix.length && length - prefix.length - suffix.length > 1;
}

/** @param {TacoLeaf} leaf @returns {Sequence} The file name parts of a sequence. */
function sequence(leaf) {
  return { prefix: leaf.key.slice(leaf.key.lastIndexOf("/") + 1), suffix: leaf.suffix, maximum: leaf.maximum };
}

/** @param {Sequence} item @returns {Sequence} */
function lower({ prefix, suffix, maximum }) {
  return { prefix: prefix.toLowerCase(), suffix: suffix.toLowerCase(), maximum };
}
