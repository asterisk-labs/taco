import { matchLeaf } from "../contract/structure.js";
import { fail } from "../errors.js";
import { matchesFilter } from "./filter.js";
import { TacoAsset } from "../container/asset.js";
import { TacoParquet, PROTECTED_LOCATION_COLUMNS } from "../container/parquet.js";
import { basename, contractPath, parentLevel } from "../container/paths.js";
import { safeInteger } from "./source.js";

const ID_CURRENT = "internal:current_id";
const ID_PARENT = "internal:parent_id";
const ID_PATH = "internal:relative_path";
const ID_OFFSET = "internal:offset";
const ID_SIZE = "internal:size";
const ID_SOURCE = "internal:source_file";
const SAMPLE_INDEX = "taco:sample_index";
const HEADER_FIELD = "rumi:header";

/**
 * @typedef {Record<string, any>} Row
 * @typedef {import("../contract/structure.js").TacoLeaf} TacoLeaf
 * @typedef {object} Node
 * @property {Row} row
 * @property {string} level
 * @property {Node | null} parent
 * @property {Node} sample
 *
 * @typedef {object} WideColumns
 * @property {string} location
 * @property {string | null} header
 *
 * @typedef {object} ReadLevelOptions
 * @property {string[]} [columns]
 * @property {Record<string, any>} [filter]
 * @property {number} [rowStart]
 * @property {number} [rowEnd]
 * @property {number[]} [rowIndexes]
 *
 * @typedef {object} ReadOptions
 * @property {"wide" | "long"} [layout]
 * @property {number | [number, number] | null} [idx]
 * @property {string | null} [level]
 * @property {string[] | null} [files]
 * @property {boolean} [location]
 * @property {Record<string, any>} [filter]
 */

export class Dataset {
  /** @type {import("./source.js").Source} */
  #source;
  /** @type {import("../container/http.js").HttpClient} */
  #client;
  /** @type {Map<string, Promise<TacoParquet>>} */
  #parquets;

  /**
   * @param {import("./source.js").Source} source
   * @param {import("../container/http.js").HttpClient} client
   */
  constructor(source, client) {
    this.url = source.url;
    this.sources = [source.url];
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
   * @param {string} level
   * @param {{onProgress?: (progress: {loaded: number, total: number}) => void}} [options]
   */
  async cacheLevel(level, options = {}) {
    if (typeof level !== "string" || !this.levels.includes(level)) {
      fail("UNKNOWN_LEVEL", `unknown metadata level ${JSON.stringify(level)}`);
    }
    if (options === null || typeof options !== "object" || Array.isArray(options)) {
      throw new TypeError("taco: cache options must be an object");
    }
    if (options.onProgress !== undefined && typeof options.onProgress !== "function") {
      throw new TypeError("taco: onProgress must be a function");
    }
    await (await this.#levelReader(level)).cache(options.onProgress);
  }

  /** @param {string} level Return the number of rows in one metadata level without decoding it. */
  async levelRowCount(level) {
    if (typeof level !== "string" || !this.levels.includes(level)) {
      fail("UNKNOWN_LEVEL", `unknown metadata level ${JSON.stringify(level)}`);
    }
    return (await this.#levelReader(level)).rowCount();
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
    const level = options.level ?? null;
    if (level !== null) {
      if (typeof level !== "string" || level.length === 0) {
        throw new TypeError("taco: level must be a non-empty string or null");
      }
      /** @type {ReadLevelOptions} */
      const rawOptions = {};
      if (typeof idx === "number") {
        rawOptions.rowStart = idx;
        rawOptions.rowEnd = idx + 1;
      } else if (idx !== null) {
        rawOptions.rowStart = idx[0];
        rawOptions.rowEnd = idx[1];
      }
      if (options.filter) rawOptions.filter = options.filter;
      return this.readLevel(level, rawOptions);
    }
    const leaves = this.#selectedLeaves(options.files);
    const samples = await this.#sampleRows(idx);

    const rows = layout === "wide"
      ? await this.#wideRows(samples, leaves, location)
      : await this.#longRows(samples, leaves, location);
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
      ? [...requestedSet].filter((name) => !this.structure.includes(name))
      : [];
    if (unknown.length) {
      fail("INVALID_FILES", `files contains unknown structure leaves: ${unknown.join(", ")}`);
    }
    const leaves = this.#source.parsed.leaves.filter(
      (leaf) => requestedSet === null || requestedSet.has(leaf.declaration),
    );
    if (leaves.length === 0) fail("INVALID_FILES", "no structure leaf matches files");
    return leaves;
  }

  /**
   * @param {Row[]} samples
   * @param {TacoLeaf[]} leaves
   * @param {boolean} location
   */
  async #wideRows(samples, leaves, location) {
    // Seed every sample before reading children. This keeps samples with
    // missing optional files and gives variable leaves an empty-list default.
    const columns = new Map(leaves.map((leaf) => [leaf, this.#wideColumns(leaf)]));
    const outputs = new Map();
    for (const sample of samples) {
      const output = this.#sampleIdentity(sample);
      copyUserMetadata(output, sample);
      for (const leaf of leaves) {
        for (const name of Object.values(/** @type {WideColumns} */ (columns.get(leaf)))) {
          if (name) output[name] = location && leaf.variable ? [] : null;
        }
      }
      outputs.set(this.#identity(sample), output);
    }
    if (!location || samples.length === 0) return [...outputs.values()];

    const files = await this.#fileNodes(samples, leaves, true);
    // Variable leaves are collected with their numeric index and sorted only
    // after the hierarchy has been walked.
    /** @type {Map<string, Map<TacoLeaf, Array<{ index: number, location: string, header: any }>>>} */
    const sequences = new Map();
    for (const node of files) {
      const path = contractPath(requirePath(node.row));
      const leaf = leaves.find((candidate) => matchLeaf(candidate, path) !== null);
      if (!leaf) continue;
      const sampleKey = this.#identity(node.sample.row);
      const output = outputs.get(sampleKey);
      if (!output) continue;
      const names = /** @type {WideColumns} */ (columns.get(leaf));
      const value = this.#source.location(node.row);
      const header = node.row[HEADER_FIELD] ?? null;
      if (!leaf.variable) {
        output[names.location] = value;
        if (names.header) output[names.header] = header;
        continue;
      }
      let sampleSequences = sequences.get(sampleKey);
      if (!sampleSequences) {
        sampleSequences = new Map();
        sequences.set(sampleKey, sampleSequences);
      }
      let values = sampleSequences.get(leaf);
      if (!values) {
        values = [];
        sampleSequences.set(leaf, values);
      }
      values.push({ index: /** @type {number} */ (matchLeaf(leaf, path)), location: value, header });
    }
    for (const [sampleKey, sampleSequences] of sequences) {
      const output = outputs.get(sampleKey);
      if (!output) continue;
      for (const [leaf, values] of sampleSequences) {
        const names = /** @type {WideColumns} */ (columns.get(leaf));
        values.sort((left, right) => left.index - right.index);
        output[names.location] = values.map((item) => item.location);
        if (names.header) output[names.header] = values.map((item) => item.header);
      }
    }
    return [...outputs.values()];
  }

  /**
   * Wide column names of one leaf, named after its structure path. Metadata
   * fields contain exactly one ':', so the double separator cannot collide
   * with user metadata. Rumi assets are read statelessly with their header, so
   * it travels next to the location.
   *
   * @param {TacoLeaf} leaf
   * @returns {WideColumns}
   */
  #wideColumns(leaf) {
    const slash = leaf.key.lastIndexOf("/");
    const level = slash < 0 ? "children" : `children/${leaf.key.slice(0, slash)}`;
    const fields = this.contract.metadata[level] ?? {};
    return { location: `${leaf.key}::location`, header: HEADER_FIELD in fields ? `${leaf.key}::header` : null };
  }

  /**
   * @param {Row[]} samples
   * @param {TacoLeaf[]} leaves
   * @param {boolean} location
   */
  async #longRows(samples, leaves, location) {
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
    // Reconstruct the metadata tree level by level. Each read is restricted to
    // known parent ids, so unrelated Parquet rows are never decoded.
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
            ...(HEADER_FIELD in (this.contract.metadata[level] ?? {}) ? [HEADER_FIELD] : []),
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
    return { [ID_PARENT]: { $in: [...new Set(parents.map((node) => node.row[ID_CURRENT]))] } };
  }

  /** @param {Row} row */
  #identity(row) {
    return String(safeInteger(row[ID_CURRENT], ID_CURRENT));
  }

  /** @param {Row} row */
  #parentIdentity(row) {
    return String(safeInteger(row[ID_PARENT], ID_PARENT));
  }

  /** @param {Row} sample */
  #sampleIdentity(sample) {
    /** @type {Row} */
    const output = {};
    if (this.container === "tacocat") output.source_file = requireSource(sample);
    output[SAMPLE_INDEX] = safeInteger(sample[ID_CURRENT], ID_CURRENT);
    output.id = sample.id;
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
