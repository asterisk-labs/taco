import { fail } from "./errors.js";
import { arrayBuffer, httpUrl } from "./http.js";

export class TacoAsset {
  /** @type {import("./http.js").HttpClient} */
  #client;

  /**
   * @param {string} location
   * @param {import("./http.js").HttpClient} client
   */
  constructor(location, client) {
    if (typeof location !== "string" || location.length === 0) {
      throw new TypeError("taco: asset location must be a non-empty string");
    }
    this.location = location;
    this.#client = client;

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

    this.url = httpUrl(location);
    this.offset = null;
    this.size = null;
  }

  /** @returns {Promise<ArrayBuffer>} */
  async arrayBuffer() {
    if (this.offset === null || this.size === null) {
      return arrayBuffer(await this.#client.get(this.url));
    }
    const result = await this.#client.range(this.url, this.offset, this.offset + this.size - 1);
    if (result.bytes.length !== this.size) {
      fail(
        "TRUNCATED_ASSET",
        `asset returned ${result.bytes.length} bytes; expected ${this.size}`,
      );
    }
    return arrayBuffer(result.bytes);
  }

  /**
   * @param {string} [type]
   * @returns {Promise<Blob>}
   */
  async blob(type = "application/octet-stream") {
    return new Blob([await this.arrayBuffer()], { type });
  }
}
