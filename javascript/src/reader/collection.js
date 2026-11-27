import { parseCollection } from "../contract/collection.js";
import { fail } from "../errors.js";


/**
 * Parse and validate a collection document.
 *
 * @param {Uint8Array} bytes
 * @param {string} source
 */
export function parseCollectionJson(bytes, source) {
  let value;
  try {
    // Keep a byte order mark, so JSON.parse rejects it as the native reader does.
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes));
  } catch (error) {
    fail("INVALID_COLLECTION", `could not parse ${source}: ${error instanceof Error ? error.message : error}`);
  }
  return parseCollection(value);
}


/**
 * Load one remote collection document.
 *
 * @param {import("../container/http.js").HttpClient} client
 * @param {string} url
 */
export async function loadCollection(client, url) {
  return parseCollectionJson(await client.get(url), url);
}
