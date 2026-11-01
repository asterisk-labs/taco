import { fail } from "../errors.js";
import { arrayBuffer, httpUrl } from "./http.js";

/** @param {Promise<unknown>} promise @param {AbortSignal | undefined} signal */
async function waitFor(promise, signal) {
  if (!signal) {
    try {
      await promise;
    } catch {}
    return;
  }
  signal.throwIfAborted();
  let abort = () => {};
  const canceled = new Promise((_, reject) => {
    abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
  });
  try {
    await Promise.race([promise.catch(() => {}), canceled]);
  } finally {
    signal.removeEventListener("abort", abort);
  }
}

export class TacoAsset {
  /** @type {import("./http.js").HttpClient} */
  #client;

  /** @type {number | null} */
  #sourceSize = null;

  /** @type {Uint8Array | null} */
  #fullBytes = null;

  /** @type {string | null} */
  #etag = null;

  /** @type {{ signal: AbortSignal | undefined, maxFullBytes: number | undefined, promise: Promise<number> }[]} */
  #sizeReads = [];

  /** @type {Promise<Uint8Array> | null} */
  #initialRead = null;

  #rangeSupported = false;

  /**
   * @param {string} location
   * @param {import("./http.js").HttpClient} client
   * @param {{ sourceSize?: number, etag?: string | null, fullBytes?: Uint8Array | null }} [snapshot]
   */
  constructor(location, client, snapshot = {}) {
    if (typeof location !== "string" || location.length === 0) {
      throw new TypeError("taco: asset location must be a non-empty string");
    }
    this.location = location;
    this.#client = client;
    this.#sourceSize = snapshot.sourceSize ?? null;
    this.#etag = snapshot.etag ?? null;
    this.#fullBytes = snapshot.fullBytes ?? null;

    const subfile = location.match(/^\/vsisubfile\/(\d+)_(\d+),\/vsicurl\/(https?:\/\/.+)$/);
    if (subfile) {
      this.offset = Number(subfile[1]);
      this.size = Number(subfile[2]);
      this.url = httpUrl(subfile[3]);
      if (
        !Number.isSafeInteger(this.offset) ||
        !Number.isSafeInteger(this.size) ||
        this.offset < 0 ||
        this.size <= 0 ||
        !Number.isSafeInteger(this.offset + this.size)
      ) {
        fail("INVALID_LOCATION", `invalid subfile location ${JSON.stringify(location)}`);
      }
      return;
    }

    this.url = httpUrl(location.replace(/^\/vsicurl\/(?=https?:\/\/)/, ""));
    this.offset = null;
    this.size = null;
  }

  /**
   * @param {{ signal?: AbortSignal, maxFullBytes?: number }} [options]
   * @returns {Promise<number>}
   */
  async byteLength(options = {}) {
    options.signal?.throwIfAborted();
    if (this.size !== null) return this.size;
    if (this.#sourceSize !== null) return this.#sourceSize;
    const existing = this.#sizeReads.find(
      (read) => read.signal === options.signal && read.maxFullBytes === options.maxFullBytes,
    );
    if (existing) return existing.promise;
    const entry = {
      signal: options.signal,
      maxFullBytes: options.maxFullBytes,
      promise: this.#client.range(
        this.url,
        0,
        0,
        undefined,
        { signal: options.signal, maxFullBytes: options.maxFullBytes },
      ).then((result) => {
        this.#remember(result);
        return result.totalSize;
      }),
    };
    this.#sizeReads.push(entry);
    try {
      return await entry.promise;
    } finally {
      this.#sizeReads.splice(this.#sizeReads.indexOf(entry), 1);
    }
  }

  /**
   * @param {number} offset
   * @param {number} length
   * @param {{ signal?: AbortSignal, maxFullBytes?: number }} [options]
   * @returns {Promise<Uint8Array>}
   */
  async read(offset, length, options = {}) {
    options.signal?.throwIfAborted();
    if (
      !Number.isSafeInteger(offset) ||
      !Number.isSafeInteger(length) ||
      offset < 0 ||
      length < 0
    ) {
      throw new RangeError(`taco: invalid asset range ${offset}+${length}`);
    }
    const size = await this.byteLength(options);
    if (offset > size || length > size - offset) {
      throw new RangeError(`taco: invalid asset range ${offset}+${length}`);
    }
    if (length === 0) return new Uint8Array();

    const base = this.offset ?? 0;
    const start = base + offset;
    const end = start + length;
    const cached = this.#cached(start, end, length, options.maxFullBytes);
    if (cached) return cached;

    if (this.#rangeSupported) {
      return this.#readRange(start, end, length, options);
    }

    if (this.#initialRead) {
      await waitFor(this.#initialRead, options.signal);
      options.signal?.throwIfAborted();
      const bytes = this.#cached(start, end, length, options.maxFullBytes);
      if (bytes) return bytes;
      return this.#readRange(start, end, length, options);
    }

    const initial = this.#readRange(start, end, length, options);
    this.#initialRead = initial;
    try {
      return await initial;
    } finally {
      if (this.#initialRead === initial) this.#initialRead = null;
    }
  }

  /** @param {number} start @param {number} end @param {number} length @param {number | undefined} maxFullBytes */
  #cached(start, end, length, maxFullBytes) {
    if (!this.#fullBytes) return null;
    if (maxFullBytes !== undefined && this.#fullBytes.byteLength > maxFullBytes) {
      fail(
        "FULL_RESPONSE_TOO_LARGE",
        `server ignored Range and returned more than ${maxFullBytes} bytes for ${this.url}`,
      );
    }
    const bytes = this.#fullBytes.subarray(start, end);
    if (bytes.byteLength !== length) {
      fail("TRUNCATED_ASSET", `asset returned ${bytes.byteLength} bytes; expected ${length}`);
    }
    return bytes.slice();
  }

  /**
   * @param {number} start
   * @param {number} end
   * @param {number} length
   * @param {{ signal?: AbortSignal, maxFullBytes?: number }} options
   */
  async #readRange(start, end, length, options) {
    const result = await this.#client.range(
      this.url,
      start,
      end - 1,
      undefined,
      {
        signal: options.signal,
        maxFullBytes: options.maxFullBytes,
      },
    );
    this.#remember(result);
    if (result.fullBytes === null) this.#rangeSupported = true;
    if (result.bytes.byteLength !== length) {
      fail(
        "TRUNCATED_ASSET",
        `asset returned ${result.bytes.byteLength} bytes; expected ${length}`,
      );
    }
    return result.fullBytes ? result.bytes.slice() : result.bytes;
  }

  /**
   * @param {{ totalSize: number, fullBytes: Uint8Array | null, etag: string | null }} result
   */
  #remember(result) {
    if (this.#sourceSize !== null && result.totalSize !== this.#sourceSize) {
      fail(
        "SOURCE_CHANGED",
        `object size changed while reading ${this.url} (${this.#sourceSize} to ${result.totalSize})`,
      );
    }
    if (this.#etag !== null && result.etag !== this.#etag) {
      fail("SOURCE_CHANGED", `object changed while reading ${this.url}`);
    }
    this.#sourceSize = result.totalSize;
    if (this.#etag === null && result.etag !== null) this.#etag = result.etag;
    if (result.fullBytes) this.#fullBytes = result.fullBytes;
  }

  /** @returns {Promise<ArrayBuffer>} */
  async arrayBuffer() {
    if (this.offset === null && this.#sourceSize === null) {
      const bytes = await this.#client.get(this.url);
      this.#sourceSize = bytes.byteLength;
      this.#fullBytes = bytes;
      return arrayBuffer(bytes.slice());
    }
    return arrayBuffer(await this.read(0, await this.byteLength()));
  }

  /**
   * @param {string} [type]
   * @returns {Promise<Blob>}
   */
  async blob(type = "application/octet-stream") {
    return new Blob([await this.arrayBuffer()], { type });
  }
}
