import { matchLeaf } from "./contract.js";
import { fail } from "./errors.js";
import { matchesFilter } from "./filter.js";
import { TacoParquet, PROTECTED_LOCATION_COLUMNS } from "./parquet.js";
import { basename, contractPath, parentLevel } from "./paths.js";
import { safeInteger } from "./source.js";
import { TacoAsset } from "./asset.js";

const ID_CURRENT = "internal:current_id";
const ID_PARENT = "internal:parent_id";
const ID_PATH = "internal:relative_path";
const ID_OFFSET = "internal:offset";
const ID_SIZE = "internal:size";
const ID_SOURCE = "internal:source_file";

/**
 * @typedef {Record<string, any>} Row
 * @typedef {import("./contract.js").TacoLeaf} TacoLeaf
 * @typedef {object} Node
 * @property {Row} row
 * @property {string} level
 * @property {Node | null} parent
 * @property {Node} sample
 *
 * @typedef {object} ReadLevelOptions
 * @property {string[]} [columns]
 * @property {Record<string, any>} [filter]
 * @property {number} [rowStart]
 * @property {number} [rowEnd]
 *
 * @typedef {object} ReadOptions
 * @property {"wide" | "long"} [layout]
 * @property {number | [number, number] | null} [idx]
 * @property {string[] | null} [files]
 * @property {boolean} [location]
 * @property {Record<string, any>} [filter]
 */

export class Dataset {
  /** @type {import("./source.js").Source} */
  #source;
  /** @type {import("./http.js").HttpClient} */
  #client;
  /** @type {Map<string, Promise<TacoParquet>>} */
  #parquets;

  /**
   * @param {import("./source.js").Source} source
   * @param {import("./http.js").HttpClient} client
   */
  constructor(source, client) {
    this.url = source.url;
    this.container = source.container;
    this.profile = "taco";
    this.collection = source.parsed.collection;
    this.contract = source.parsed.contract;
    this.structure = source.parsed.contract.structure;
    this.levels = [...source.parsed.levels];
    this.derived = source.parsed.contract.derived;
    this.#source = source;
    this.#client = client;
    this.#parquets = new Map();
  }

  /**
   * Read one metadata level without synthesizing any location column.
   *
   * @param {string} level
   * @param {ReadLevelOptions} [options]
   * @returns {Promise<Row[]>}
   */
  async readLevel(level, options = {}) {
    if (typeof level !== "string" || !this.levels.includes(level)) {
      fail("UNKNOWN_LEVEL", `unknown metadata level ${JSON.stringify(level)}`);
    }
    return (await this.#levelReader(level)).read(options);
  }

  /**
   * Read the dataset as one row per sample (wide) or one row per file (long).
   *
   * @param {ReadOptions} [options]
   * @returns {Promise<Row[]>}
   */
  async read(options = {}) {
    if (options === null || typeof options !== "object" || Array.isArray(options)) {
      throw new TypeError("taco: read options must be an object");
    }
    const layout = options.layout ?? "wide";
    if (layout !== "wide" && layout !== "long") {
      throw new TypeError("taco: layout must be wide or long");
    }
    const location = options.location ?? true;
    if (typeof location !== "boolean") throw new TypeError("taco: location must be a boolean");
    const idx = normalizeIdx(options.idx);
    const leaves = this.#selectedLeaves(options.files);
    const samples = await this.#sampleRows(idx);

    let rows;
    if (this.structure === null) {
      rows = samples.map((sample) => this.#nullStructureRow(sample, location));
    } else if (layout === "wide") {
      rows = await this.#wideRows(samples, leaves, location);
    } else {
      rows = await this.#longRows(samples, leaves, location);
    }
    return options.filter ? rows.filter((row) => matchesFilter(row, options.filter)) : rows;
  }

  /**
   * Convert a generated TACO location into a browser-readable asset.
   *
   * @param {string} location
   */
  resolveAsset(location) {
    return new TacoAsset(location, this.#client);
  }

  /** @param {string} level */
  #levelReader(level) {
    let promise = this.#parquets.get(level);
    if (!promise) {
      promise = this.#source.levelBuffer(level).then((file) => new TacoParquet(file, level));
      this.#parquets.set(level, promise);
    }
    return promise;
  }

  /** @param {number | [number, number] | null} idx */
  async #sampleRows(idx) {
    /** @type {ReadLevelOptions} */
    const options = {};
    if (this.container !== "tacocat" && idx !== null) {
      if (typeof idx === "number") {
        options.rowStart = idx;
        options.rowEnd = idx + 1;
      } else {
        options.rowStart = idx[0];
        options.rowEnd = idx[1];
      }
    }
    const rows = await this.readLevel("sample", options);
    if (this.container !== "tacocat" || idx === null) return rows;
    return rows.filter((row) => {
      const id = safeInteger(row[ID_CURRENT], ID_CURRENT);
      return typeof idx === "number" ? id === idx : id >= idx[0] && id < idx[1];
    });
  }

  /** @param {string[] | null | undefined} requested */
  #selectedLeaves(requested) {
    if (this.structure === null) {
      if (requested !== undefined && requested !== null) {
        fail("INVALID_FILES", "files requires a non-null taco:structure");
      }
      return null;
    }
    if (
      requested !== undefined &&
      requested !== null &&
      (!Array.isArray(requested) ||
        requested.some((value) => typeof value !== "string" || value.length === 0))
    ) {
      throw new TypeError("taco: files must be an array of non-empty strings");
    }
    const requestedSet = requested == null ? null : new Set(requested);
    const unknown = requestedSet
      ? [...requestedSet].filter((name) => !this.structure?.includes(name))
      : [];
    if (unknown.length) {
      fail("INVALID_FILES", `files contains unknown structure leaves: ${unknown.join(", ")}`);
    }
    const leaves = (this.#source.parsed.leaves ?? []).filter(
      (leaf) => requestedSet === null || requestedSet.has(leaf.declaration),
    );
    if (leaves.length === 0) fail("INVALID_FILES", "no structure leaf matches files");
    return leaves;
  }

  /** @param {Row} sample @param {boolean} location */
  #nullStructureRow(sample, location) {
    const output = this.#sampleIdentity(sample);
    output["taco:location"] = location ? this.#source.location(sample) : null;
    copyUserMetadata(output, sample);
    return output;
  }

  /**
   * @param {Row[]} samples
   * @param {TacoLeaf[] | null} leaves
   * @param {boolean} location
   */
  async #wideRows(samples, leaves, location) {
    if (!leaves) throw new Error("taco: internal null-structure mismatch");
    const outputs = new Map();
    for (const sample of samples) {
      const output = this.#sampleIdentity(sample);
      copyUserMetadata(output, sample);
      for (const leaf of leaves) output[leaf.key] = location ? (leaf.variable ? [] : null) : null;
      outputs.set(this.#identity(sample), output);
    }
    if (!location || samples.length === 0) return [...outputs.values()];

    const files = await this.#fileNodes(samples, leaves, true);
    /** @type {Map<string, Map<string, Array<{ index: number, location: string }>>>} */
    const sequences = new Map();
    for (const node of files) {
      const path = contractPath(requirePath(node.row));
      const leaf = leaves.find((candidate) => matchLeaf(candidate, path) !== null);
      if (!leaf) continue;
      const sampleKey = this.#identity(node.sample.row);
      const output = outputs.get(sampleKey);
      if (!output) continue;
      const value = this.#source.location(node.row);
      if (!leaf.variable) {
        output[leaf.key] = value;
        continue;
      }
      let sampleSequences = sequences.get(sampleKey);
      if (!sampleSequences) {
        sampleSequences = new Map();
        sequences.set(sampleKey, sampleSequences);
      }
      let values = sampleSequences.get(leaf.key);
      if (!values) {
        values = [];
        sampleSequences.set(leaf.key, values);
      }
      values.push({ index: /** @type {number} */ (matchLeaf(leaf, path)), location: value });
    }
    for (const [sampleKey, sampleSequences] of sequences) {
      const output = outputs.get(sampleKey);
      if (!output) continue;
      for (const [name, values] of sampleSequences) {
        output[name] = values.sort((left, right) => left.index - right.index).map((item) => item.location);
      }
    }
    return [...outputs.values()];
  }

  /**
   * @param {Row[]} samples
   * @param {TacoLeaf[] | null} leaves
   * @param {boolean} location
   */
  async #longRows(samples, leaves, location) {
    if (!leaves) throw new Error("taco: internal null-structure mismatch");
    const files = await this.#fileNodes(samples, leaves, false);
    return files.map((node) => {
      const output = this.#sampleIdentity(node.sample.row);
      output.path = contractPath(requirePath(node.row));
      output["taco:location"] = location ? this.#source.location(node.row) : null;
      /** @type {Node | null} */
      let current = node;
      while (current) {
        copyUserMetadata(output, current.row, true);
        current = current.parent;
      }
      return output;
    });
  }

  /**
   * @param {Row[]} samples
   * @param {TacoLeaf[]} leaves
   * @param {boolean} identityOnly
   * @returns {Promise<Node[]>}
   */
  async #fileNodes(samples, leaves, identityOnly) {
    /** @type {Map<string, Map<string, Node>>} */
    const nodes = new Map();
    /** @type {Node[]} */
    const sampleNodes = samples.map((row) => {
      /** @type {Node} */
      const node = { row, level: "sample", parent: null, sample: /** @type {any} */ (null) };
      node.sample = node;
      return node;
    });
    nodes.set("sample", new Map(sampleNodes.map((node) => [this.#identity(node.row), node])));

    const directFolders = directFolderNames(this.levels);
    /** @type {Node[]} */
    const files = [];
    for (const level of this.levels.slice(1)) {
      const parent = parentLevel(level);
      const parentNodes = [...(nodes.get(parent)?.values() ?? [])];
      /** @type {Map<string, Node>} */
      const currentNodes = new Map();
      nodes.set(level, currentNodes);
      if (parentNodes.length === 0) continue;

      const columns = identityOnly
        ? [
            ID_CURRENT,
            ID_PARENT,
            ID_PATH,
            ...(this.container === "folder" ? [] : [ID_OFFSET, ID_SIZE]),
            ...(this.container === "tacocat" ? [ID_SOURCE] : []),
          ]
        : undefined;
      const rows = await this.readLevel(level, {
        columns,
        filter: this.#parentFilter(parentNodes),
      });
      for (const row of rows) {
        const parentNode = nodes.get(parent)?.get(this.#parentIdentity(row));
        if (!parentNode) continue;
        /** @type {Node} */
        const node = { row, level, parent: parentNode, sample: parentNode.sample };
        currentNodes.set(this.#identity(row), node);

        const path = requirePath(row);
        if (directFolders.get(level)?.has(basename(path))) continue;
        const relative = contractPath(path);
        if (leaves.some((leaf) => matchLeaf(leaf, relative) !== null)) files.push(node);
      }
    }
    return files;
  }

  /** @param {Node[]} parents */
  #parentFilter(parents) {
    if (this.container !== "tacocat") {
      return { [ID_PARENT]: { $in: [...new Set(parents.map((node) => node.row[ID_CURRENT]))] } };
    }
    return {
      $or: parents.map((node) => ({
        $and: [
          { [ID_SOURCE]: { $eq: requireSource(node.row) } },
          { [ID_PARENT]: { $eq: node.row[ID_CURRENT] } },
        ],
      })),
    };
  }

  /** @param {Row} row */
  #identity(row) {
    const source = this.container === "tacocat" ? requireSource(row) : "";
    return `${source}\0${safeInteger(row[ID_CURRENT], ID_CURRENT)}`;
  }

  /** @param {Row} row */
  #parentIdentity(row) {
    const source = this.container === "tacocat" ? requireSource(row) : "";
    return `${source}\0${safeInteger(row[ID_PARENT], ID_PARENT)}`;
  }

  /** @param {Row} sample */
  #sampleIdentity(sample) {
    /** @type {Row} */
    const output = { sample_id: safeInteger(sample[ID_CURRENT], ID_CURRENT) };
    if (this.container === "tacocat") output.source_file = requireSource(sample);
    return output;
  }
}

/** @param {number | [number, number] | null | undefined} value */
function normalizeIdx(value) {
  if (value === undefined || value === null) return null;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || value < 0) {
      throw new TypeError("taco: idx must contain non-negative integers");
    }
    return value;
  }
  if (
    !Array.isArray(value) ||
    value.length !== 2 ||
    value.some((item) => !Number.isSafeInteger(item) || item < 0)
  ) {
    throw new TypeError("taco: idx must be an integer or a two-element range");
  }
  if (value[0] > value[1]) throw new RangeError("taco: idx range start must not exceed its end");
  return /** @type {[number, number]} */ (value);
}

/** @param {Row} output @param {Row} input @param {boolean} [keepNearest] */
function copyUserMetadata(output, input, keepNearest = false) {
  for (const [name, value] of Object.entries(input)) {
    if (name.startsWith("internal:") || PROTECTED_LOCATION_COLUMNS.has(name)) continue;
    if (!keepNearest || !(name in output)) output[name] = value;
  }
}

/** @param {Row} row */
function requirePath(row) {
  const value = row[ID_PATH];
  if (typeof value !== "string" || value.length === 0) {
    fail("INVALID_METADATA", `${ID_PATH} must be a non-empty string`);
  }
  return value;
}

/** @param {Row} row */
function requireSource(row) {
  const value = row[ID_SOURCE];
  if (typeof value !== "string" || value.length === 0) {
    fail("INVALID_METADATA", `${ID_SOURCE} must be a non-empty string in TACOCAT`);
  }
  return value;
}

/** @param {string[]} levels */
function directFolderNames(levels) {
  /** @type {Map<string, Set<string>>} */
  const result = new Map();
  for (const level of levels) result.set(level, new Set());
  for (const level of levels) {
    if (!level.startsWith("children/")) continue;
    result.get(parentLevel(level))?.add(basename(level));
  }
  return result;
}
