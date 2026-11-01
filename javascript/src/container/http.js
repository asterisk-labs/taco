import { fail } from "../errors.js";

/** @typedef {typeof globalThis.fetch} FetchFunction */
/** @typedef {(progress: {loaded: number, total: number}) => void} ProgressCallback */

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
 * @param {AbortSignal | null | undefined} local
 * @param {AbortSignal | null | undefined} shared
 * @returns {AbortSignal | undefined}
 */
function combinedSignal(local, shared) {
  if (!local) return shared ?? undefined;
  if (!shared || local === shared) return local;
  const signals = [local, shared];
  if (typeof AbortSignal.any === "function") return AbortSignal.any(signals);
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      break;
    }
    const abort = () => controller.abort(signal.reason);
    signal.addEventListener("abort", abort, { once: true, signal: controller.signal });
  }
  return controller.signal;
}

/** @param {string | null} previous @param {string | null} current @param {string} url */
function updateEtag(previous, current, url) {
  if (previous !== null && current !== previous) {
    fail("SOURCE_CHANGED", `object changed while reading ${url}`);
  }
  return previous ?? current;
}

/**
 * @param {Uint8Array} bytes
 * @returns {ArrayBuffer}
 */
export function arrayBuffer(bytes) {
  if (bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength) {
    return /** @type {ArrayBuffer} */ (bytes.buffer);
  }
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
    const response = await this.getResponse(url);
    if (!response.ok) {
      fail("HTTP_ERROR", `HTTP ${response.status} ${response.statusText} for ${url}`);
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  /**
   * Fetch an object, returning null only when the server answers with 404.
   *
   * @param {string} url
   * @returns {Promise<Uint8Array | null>}
   */
  async getOptional(url) {
    const response = await this.getResponse(url);
    if (response.status === 404) return null;
    if (!response.ok) {
      fail("HTTP_ERROR", `HTTP ${response.status} ${response.statusText} for ${url}`);
    }
    return new Uint8Array(await response.arrayBuffer());
  }

  /** @param {string} url @returns {Promise<Response>} */
  async getResponse(url) {
    const headers = new Headers(this.requestInit.headers);
    const response = await this.fetch(url, {
      ...this.requestInit,
      method: "GET",
      headers,
    });
    return response;
  }

  /**
   * @param {string} url
   * @param {number} start
   * @param {number} end Inclusive end byte.
   * @param {ProgressCallback} [onProgress]
   * @param {{ signal?: AbortSignal, maxFullBytes?: number }} [options]
   * @returns {Promise<{bytes: Uint8Array, totalSize: number, fullBytes: Uint8Array | null, etag: string | null}>}
   */
  async range(url, start, end, onProgress, options = {}) {
    if (
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      start < 0 ||
      end < start
    ) {
      throw new RangeError(`taco: invalid byte range ${start}-${end}`);
    }
    if (options.maxFullBytes !== undefined && (
      !Number.isSafeInteger(options.maxFullBytes) || options.maxFullBytes <= 0
    )) {
      throw new RangeError("taco: maxFullBytes must be a positive safe integer");
    }
    const headers = new Headers(this.requestInit.headers);
    headers.set("Range", `bytes=${start}-${end}`);
    const response = await this.fetch(url, {
      ...this.requestInit,
      method: "GET",
      headers,
      signal: combinedSignal(options.signal, this.requestInit.signal),
    });
    if (response.status === 412) fail("SOURCE_CHANGED", `object changed while reading ${url}`);
    if (response.status !== 206 && response.status !== 200) {
      fail("HTTP_ERROR", `HTTP ${response.status} ${response.statusText} for ${url}`);
    }
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
      const responseBytes = await readResponseBytes(response, expected, onProgress);
      if (responseBytes.length !== expected) {
        fail(
          "INVALID_RANGE",
          `range response for ${url} has ${responseBytes.length} bytes; expected ${expected}`,
        );
      }
      return {
        bytes: responseBytes,
        totalSize,
        fullBytes: null,
        etag: response.headers.get("etag"),
      };
    }

    const lengthHeader = response.headers.get("content-length");
    const encoded = response.headers.has("content-encoding");
    let expected;
    if (!encoded && lengthHeader !== null) {
      const declaredLength = Number(lengthHeader);
      if (Number.isSafeInteger(declaredLength) && declaredLength >= 0) {
        expected = declaredLength;
      }
    }
    if (expected !== undefined && options.maxFullBytes !== undefined && expected > options.maxFullBytes) {
      await response.body?.cancel();
      fail("FULL_RESPONSE_TOO_LARGE", `server ignored Range and returned more than ${options.maxFullBytes} bytes for ${url}`);
    }
    const responseBytes = await readResponseBytes(
      response,
      expected,
      onProgress,
      options.maxFullBytes,
    );
    if (responseBytes.length === 0 || start >= responseBytes.length) {
      fail("INVALID_RANGE", `server ignored Range and did not return byte ${start} for ${url}`);
    }
    return {
      bytes: responseBytes.subarray(start, Math.min(end + 1, responseBytes.length)),
      totalSize: responseBytes.length,
      fullBytes: responseBytes,
      etag: response.headers.get("etag"),
    };
  }
}

export class HttpObject {
  /**
   * @param {HttpClient} client
   * @param {string} url
   * @param {number} byteLength
   * @param {Uint8Array | null} [fullBytes]
   * @param {string | null} [etag]
   */
  constructor(client, url, byteLength, fullBytes = null, etag = null) {
    this.client = client;
    this.url = url;
    this.byteLength = byteLength;
    this.fullBytes = fullBytes;
    this.etag = etag;
  }

  /**
   * @param {number} start
   * @param {number} end Exclusive end byte.
   * @param {ProgressCallback} [onProgress]
   * @returns {Promise<Uint8Array>}
   */
  async sliceBytes(start, end, onProgress) {
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
    if (this.fullBytes) {
      onProgress?.({ loaded: end - start, total: end - start });
      return this.fullBytes.subarray(start, end);
    }
    const result = await this.client.range(this.url, start, end - 1, onProgress);
    if (result.totalSize !== this.byteLength) {
      fail(
        "SOURCE_CHANGED",
        `object size changed while reading ${this.url} (${this.byteLength} to ${result.totalSize})`,
      );
    }
    this.etag = updateEtag(this.etag, result.etag, this.url);
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

  /** @param {ProgressCallback} [onProgress] */
  async readAll(onProgress) {
    if (this.fullBytes) {
      onProgress?.({ loaded: this.byteLength, total: this.byteLength });
      return arrayBuffer(this.fullBytes);
    }
    const result = await this.client.range(this.url, 0, this.byteLength - 1, onProgress);
    const bytes = result.fullBytes ?? result.bytes;
    if (result.totalSize !== this.byteLength || bytes.byteLength !== this.byteLength) {
      fail("SOURCE_CHANGED", `object size changed while reading ${this.url}`);
    }
    this.etag = updateEtag(this.etag, result.etag, this.url);
    this.fullBytes = bytes;
    return arrayBuffer(bytes);
  }

  /**
   * @param {number} offset
   * @param {number} size
   * @returns {{ byteLength: number, slice(start: number, end?: number): Promise<ArrayBuffer>, readAll(onProgress?: ProgressCallback): Promise<ArrayBuffer> }}
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
      readAll: async (onProgress) => arrayBuffer(
        await this.sliceBytes(offset, offset + size, onProgress),
      ),
    };
  }
}

/**
 * @param {Response} response
 * @param {number | undefined} expected
 * @param {ProgressCallback | undefined} onProgress
 * @param {number} [maxBytes]
 */
async function readResponseBytes(response, expected, onProgress, maxBytes = undefined) {
  if (!response.body || (!onProgress && expected === undefined && maxBytes === undefined)) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    onProgress?.({ loaded: bytes.byteLength, total: expected ?? bytes.byteLength });
    return bytes;
  }

  const reader = response.body.getReader();
  const output = expected === undefined ? null : new Uint8Array(expected);
  const chunks = [];
  let loaded = 0;
  let lastUpdate = Date.now();
  onProgress?.({ loaded, total: expected ?? 0 });
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (maxBytes !== undefined && loaded + value.byteLength > maxBytes) {
      await reader.cancel();
      fail("FULL_RESPONSE_TOO_LARGE", `HTTP response exceeds ${maxBytes} bytes`);
    }
    if (output) {
      if (loaded + value.byteLength > output.byteLength) {
        await reader.cancel();
        throw new Error("HTTP response exceeds its declared Content-Length");
      }
      output.set(value, loaded);
    } else {
      chunks.push(value);
    }
    loaded += value.byteLength;
    const now = Date.now();
    if (now - lastUpdate >= 100) {
      onProgress?.({ loaded, total: expected ?? 0 });
      lastUpdate = now;
    }
  }
  onProgress?.({ loaded, total: expected ?? 0 });
  if (output) return output.subarray(0, loaded);

  const bytes = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

/**
 * @param {HttpClient} client
 * @param {string} url
 * @param {number} probeEnd Inclusive end byte.
 */
export async function openHttpObject(client, url, probeEnd = 0) {
  const first = await client.range(url, 0, probeEnd);
  const object = new HttpObject(client, url, first.totalSize, first.fullBytes, first.etag);
  return { object, initialBytes: first.bytes };
}
