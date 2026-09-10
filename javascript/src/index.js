import { Dataset } from "./dataset.js";
import { HttpClient } from "./http.js";
import { openSource } from "./source.js";

/** @typedef {typeof globalThis.fetch} FetchFunction */

/**
 * @typedef {object} OpenOptions
 * @property {"auto" | "folder" | "zip" | "tacocat"} [container]
 * @property {FetchFunction} [fetch]
 * @property {RequestInit} [requestInit]
 *
 * @typedef {import("./dataset.js").ReadOptions} ReadOptions
 * @typedef {import("./dataset.js").ReadLevelOptions} ReadLevelOptions
 * @typedef {ReadOptions & OpenOptions} SourceReadOptions
 */

/**
 * Open a remote TACO FOLDER, ZIP, or TACOCAT dataset.
 *
 * @param {string} source
 * @param {OpenOptions} [options]
 * @returns {Promise<Dataset>}
 */
export async function openDataset(source, options = {}) {
  if (options === null || typeof options !== "object" || Array.isArray(options)) {
    throw new TypeError("taco: open options must be an object");
  }
  const client = new HttpClient({ fetch: options.fetch, requestInit: options.requestInit });
  const resolved = await openSource(source, client, options.container ?? "auto");
  return new Dataset(resolved, client);
}

/**
 * Open and read a remote TACO dataset in one call.
 *
 * @param {string} source
 * @param {SourceReadOptions} [options]
 */
export async function read(source, options = {}) {
  const dataset = await openDataset(source, {
    container: options.container,
    fetch: options.fetch,
    requestInit: options.requestInit,
  });
  return dataset.read({
    layout: options.layout,
    idx: options.idx,
    files: options.files,
    location: options.location,
    filter: options.filter,
  });
}

export { Dataset } from "./dataset.js";
export { TacoError } from "./errors.js";
export { SUPPORTED_TACO_VERSION } from "./contract.js";
