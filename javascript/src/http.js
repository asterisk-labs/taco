import { fail } from "./errors.js";

/** @typedef {typeof globalThis.fetch} FetchFunction */

/**
 * @param {unknown} value
 * @returns {string}
 */
export function httpUrl(value) {
  if (typeof value !== "string" || value.length === 0) {
    fail("INVALID_URL", "source URL must be a non-empty string");
  }
  if (!/^[\x01-\x7f]+$/.test(value)) {
    fail("INVALID_URL", "source URL must contain only ASCII characters");
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch (error) {
    throw new TypeError(`taco: invalid source URL ${JSON.stringify(value)}`, { cause: error });
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    fail("INVALID_URL", `only http(s) sources are supported, got ${parsed.protocol}`);
  }
  return parsed.href;
}

/** @param {string} value */
export function directoryUrl(value) {
  const parsed = new URL(value);
  if (!parsed.pathname.endsWith("/")) parsed.pathname += "/";
  return parsed.href;
}

/**
 * @param {Uint8Array} bytes
 * @returns {ArrayBuffer}
 */
export function arrayBuffer(bytes) {
  return /** @type {ArrayBuffer} */ (
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
  );
}

export class HttpClient {
  /**
   * @param {{ fetch?: FetchFunction, requestInit?: RequestInit }} [options]
   */
  constructor(options = {}) {
    const fetchFn = options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (typeof fetchFn !== "function") {
      fail("NO_FETCH", "this environment does not provide fetch()");
    }
    if (
      options.requestInit !== undefined &&
      (options.requestInit === null || typeof options.requestInit !== "object")
    ) {
      throw new TypeError("taco: requestInit must be an object");
    }
    if (options.requestInit?.body != null) {
      throw new TypeError("taco: requestInit.body is not supported for GET requests");
    }
    this.fetch = fetchFn;
    this.requestInit = options.requestInit ?? {};
  }

  /**
   * @param {string} url
   * @returns {Promise<Uint8Array>}
   */
  async get(url) {
    const headers = new Headers(this.requestInit.headers);
    const response = await this.fetch(url, {
      ...this.requestInit,
      method: "GET",
      headers,
    });
    if (!response.ok) {
      fail("HTTP_ERROR", `HTTP ${response.status} ${response.statusText} for ${url}`);
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  /**
   * @param {string} url
   * @param {number} start
   * @param {number} end Inclusive end byte.
   * @returns {Promise<{bytes: Uint8Array, totalSize: number, fullBytes: Uint8Array | null}>}
   */
  async range(url, start, end) {
    if (
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      start < 0 ||
      end < start
    ) {
      throw new RangeError(`taco: invalid byte range ${start}-${end}`);
    }
    const headers = new Headers(this.requestInit.headers);
    headers.set("Range", `bytes=${start}-${end}`);
    const response = await this.fetch(url, {
      ...this.requestInit,
      method: "GET",
      headers,
    });
    if (response.status !== 206 && response.status !== 200) {
      fail("HTTP_ERROR", `HTTP ${response.status} ${response.statusText} for ${url}`);
    }
    const responseBytes = new Uint8Array(await response.arrayBuffer());
    if (response.status === 206) {
      const contentRange = response.headers.get("content-range");
      const match = contentRange?.match(/^bytes (\d+)-(\d+)\/(\d+)$/i);
      if (!match) {
        fail("INVALID_RANGE", `range response for ${url} has no valid Content-Range`);
      }
      const responseStart = Number(match[1]);
      const responseEnd = Number(match[2]);
      const totalSize = Number(match[3]);
      if (
        !Number.isSafeInteger(totalSize) ||
        responseStart !== start ||
        responseEnd !== Math.min(end, totalSize - 1) ||
        responseEnd < responseStart ||
        totalSize <= responseEnd
      ) {
        fail("INVALID_RANGE", `invalid Content-Range ${JSON.stringify(contentRange)} for ${url}`);
      }
      const expected = responseEnd - responseStart + 1;
      if (responseBytes.length !== expected) {
        fail(
          "INVALID_RANGE",
          `range response for ${url} has ${responseBytes.length} bytes; expected ${expected}`,
        );
      }
      return { bytes: responseBytes, totalSize, fullBytes: null };
    }

    if (responseBytes.length === 0 || start >= responseBytes.length) {
      fail("INVALID_RANGE", `server ignored Range and did not return byte ${start} for ${url}`);
    }
    return {
      bytes: responseBytes.subarray(start, Math.min(end + 1, responseBytes.length)),
      totalSize: responseBytes.length,
      fullBytes: responseBytes,
    };
  }
}

export class HttpObject {
  /**
   * @param {HttpClient} client
   * @param {string} url
   * @param {number} byteLength
   * @param {Uint8Array | null} [fullBytes]
   */
  constructor(client, url, byteLength, fullBytes = null) {
    this.client = client;
    this.url = url;
    this.byteLength = byteLength;
    this.fullBytes = fullBytes;
  }

  /**
   * @param {number} start
   * @param {number} end Exclusive end byte.
   * @returns {Promise<Uint8Array>}
   */
  async sliceBytes(start, end) {
    if (
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      start < 0 ||
      end < start ||
      end > this.byteLength
    ) {
      throw new RangeError(`taco: invalid object slice ${start}-${end}`);
    }
    if (start === end) return new Uint8Array(0);
    if (this.fullBytes) return this.fullBytes.subarray(start, end);
    const result = await this.client.range(this.url, start, end - 1);
    if (result.totalSize !== this.byteLength) {
      fail(
        "SOURCE_CHANGED",
        `object size changed while reading ${this.url} (${this.byteLength} to ${result.totalSize})`,
      );
    }
    if (result.fullBytes) this.fullBytes = result.fullBytes;
    return result.bytes;
  }

  /**
   * @param {number} start
   * @param {number} [end]
   * @returns {Promise<ArrayBuffer>}
   */
  async slice(start, end = this.byteLength) {
    return arrayBuffer(await this.sliceBytes(start, end));
  }

  /**
   * @param {number} offset
   * @param {number} size
   * @returns {{ byteLength: number, slice(start: number, end?: number): Promise<ArrayBuffer> }}
   */
  subBuffer(offset, size) {
    if (
      !Number.isSafeInteger(offset) ||
      !Number.isSafeInteger(size) ||
      offset < 0 ||
      size <= 0 ||
      offset + size > this.byteLength
    ) {
      fail("INVALID_OFFSET", `invalid subfile ${offset}_${size} in ${this.url}`);
    }
    return {
      byteLength: size,
      slice: (start, end = size) => {
        if (
          !Number.isSafeInteger(start) ||
          !Number.isSafeInteger(end) ||
          start < 0 ||
          end < start ||
          end > size
        ) {
          throw new RangeError(`taco: invalid subfile slice ${start}-${end}`);
        }
        return this.slice(offset + start, offset + end);
      },
    };
  }
}

/**
 * @param {HttpClient} client
 * @param {string} url
 * @param {number} probeEnd Inclusive end byte.
 */
export async function openHttpObject(client, url, probeEnd = 0) {
  const first = await client.range(url, 0, probeEnd);
  const object = new HttpObject(client, url, first.totalSize, first.fullBytes);
  return { object, initialBytes: first.bytes };
}
