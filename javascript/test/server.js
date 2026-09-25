import http from "node:http";
import { readFile } from "node:fs/promises";

const fixturePath = new URL("../../r/tests/testthat/data/taco.zip", import.meta.url);
const nestedPath = new URL("../../core/tests/data/taco_nested.zip", import.meta.url);

/** Start a local HTTP server with ZIP and virtual FOLDER views of the fixture. */
export async function fixtureServer() {
  const archive = new Uint8Array(await readFile(fixturePath));
  const nested = new Uint8Array(await readFile(nestedPath));
  const catalogPath = new URL("../../core/tests/data/taco_cat/.tacocat/", import.meta.url);
  const catalog = new Map(await Promise.all(
    ["COLLECTION.json", "sample.parquet", "children.parquet"].map(async (name) => [
      name,
      new Uint8Array(await readFile(new URL(name, catalogPath))),
    ]),
  ));
  const entries = storedEntries(archive);
  const collection = JSON.parse(new TextDecoder().decode(entries.get("COLLECTION.json")));
  const collisionCollection = new TextEncoder().encode(JSON.stringify({
    ...collection,
    "taco:structure": ["image_bin", "mask.bin"],
    "taco:metadata": {
      ...collection["taco:metadata"],
      sample: {
        ...collection["taco:metadata"].sample,
        "image_bin:location": { type: "string", nullable: false, description: "" },
      },
    },
  }));
  /** @type {{ path: string, range: string | undefined }[]} */
  const requests = [];

  const server = http.createServer((request, response) => {
    const path = new URL(request.url ?? "/", "http://fixture.test").pathname;
    requests.push({ path, range: request.headers.range });
    if (path === "/dataset.zip") return serve(response, archive, request.headers.range);
    if (path === "/dataset") return serve(response, archive, request.headers.range);
    if (path === "/full.zip") return serve(response, archive, undefined);
    if (path === "/nested.zip") return serve(response, nested, request.headers.range);
    if (path === "/flat.zip") {
      const bytes = archive.slice();
      bytes[57] = 1;
      return serve(response, bytes, request.headers.range);
    }
    if (path === "/flat") {
      const bytes = archive.slice();
      bytes[57] = 1;
      return serve(response, bytes, request.headers.range);
    }
    if (path === "/corrupt.zip") {
      const bytes = archive.slice();
      bytes[bytes.length - 100] ^= 1;
      return serve(response, bytes, request.headers.range);
    }
    if (path === "/collision/COLLECTION.json") {
      return serve(response, collisionCollection, request.headers.range);
    }
    if (path.startsWith("/collision/")) {
      const name = decodeURIComponent(path.slice("/collision/".length));
      const bytes = entries.get(name);
      if (bytes) return serve(response, bytes, request.headers.range);
    }
    if (path.startsWith("/folder/")) {
      const name = decodeURIComponent(path.slice("/folder/".length));
      const bytes = entries.get(name);
      if (bytes) return serve(response, bytes, request.headers.range);
    }
    if (path.startsWith("/catalog/.tacocat/")) {
      const name = decodeURIComponent(path.slice("/catalog/.tacocat/".length));
      const bytes = catalog.get(name);
      if (bytes) return serve(response, bytes, request.headers.range);
    }
    response.writeHead(404);
    response.end("not found");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test server has no TCP address");
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    archive,
    entries,
    requests,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}

/**
 * Read the STORE entries by walking local file headers. CoZIP forbids data
 * descriptors, so this tiny test helper does not need a general ZIP parser.
 *
 * @param {Uint8Array} archive
 */
function storedEntries(archive) {
  const view = new DataView(archive.buffer, archive.byteOffset, archive.byteLength);
  const decoder = new TextDecoder();
  /** @type {Map<string, Uint8Array>} */
  const entries = new Map();
  let cursor = 0;
  while (cursor + 30 <= archive.length && view.getUint32(cursor, true) === 0x04034b50) {
    const method = view.getUint16(cursor + 8, true);
    const size = view.getUint32(cursor + 18, true);
    const nameLength = view.getUint16(cursor + 26, true);
    const extraLength = view.getUint16(cursor + 28, true);
    if (method !== 0) throw new Error("test fixture contains a compressed entry");
    const nameStart = cursor + 30;
    const dataStart = nameStart + nameLength + extraLength;
    const name = decoder.decode(archive.subarray(nameStart, nameStart + nameLength));
    entries.set(name, archive.slice(dataStart, dataStart + size));
    cursor = dataStart + size;
  }
  return entries;
}

/** @param {http.ServerResponse} response @param {Uint8Array} bytes @param {string | undefined} range */
function serve(response, bytes, range) {
  const match = range?.match(/^bytes=(\d+)-(\d+)$/);
  if (match) {
    const start = Number(match[1]);
    const end = Math.min(Number(match[2]), bytes.length - 1);
    if (start >= bytes.length || end < start) {
      response.writeHead(416, { "Content-Range": `bytes */${bytes.length}` });
      return response.end();
    }
    response.writeHead(206, {
      "Accept-Ranges": "bytes",
      "Content-Range": `bytes ${start}-${end}/${bytes.length}`,
      "Content-Length": end - start + 1,
    });
    return response.end(bytes.subarray(start, end + 1));
  }
  response.writeHead(200, { "Content-Length": bytes.length });
  response.end(bytes);
}
