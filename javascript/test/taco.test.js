import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { openDataset, read, SUPPORTED_TACO_VERSION, TacoError } from "../src/index.js";
import { arrayBuffer } from "../src/container/http.js";
import { parseCollection } from "../src/contract/collection.js";
import { fixtureServer } from "./server.js";

let fixture;

before(async () => {
  fixture = await fixtureServer();
});

after(async () => {
  await fixture.close();
});

test("opens a TACO-profile ZIP and exposes its contract", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  assert.equal(dataset.container, "zip");
  assert.equal(dataset.profile, "taco");
  assert.equal(dataset.collection.id, "taco-fixture");
  assert.equal(dataset.collection["taco:version"], SUPPORTED_TACO_VERSION);
  assert.deepEqual(dataset.structure, ["image.bin", "mask.bin"]);
  assert.deepEqual(dataset.levels, ["sample", "children"]);
});

test("detects a TACO-profile ZIP without relying on its URL suffix", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset`);
  assert.equal(dataset.container, "zip");
  assert.equal(dataset.collection.id, "taco-fixture");
  await assert.rejects(
    () => openDataset(`${fixture.baseUrl}/flat`),
    (error) => error instanceof TacoError && error.code === "UNKNOWN_PROFILE",
  );
});

test("preserves network failures while probing a suffixless URL", async () => {
  const unavailable = async () => new Response("unavailable", {
    status: 503,
    statusText: "Unavailable",
  });
  await assert.rejects(
    () => openDataset("https://example.test/1.0.0", { fetch: unavailable }),
    (error) => error instanceof TacoError &&
      error.code === "HTTP_ERROR" &&
      /HTTP 503.*https:\/\/example\.test\/1\.0\.0/.test(error.message),
  );
});

test("raw level reads never synthesize a location", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.readLevel("sample", {
    columns: ["internal:current_id", "ml:split", "taco:location", "cozip:location"],
    rowStart: 0,
    rowEnd: 2,
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(Object.keys(rows[0]).sort(), ["internal:current_id", "ml:split"].sort());
  assert.ok(!("taco:location" in rows[0]));
  assert.ok(!("cozip:location" in rows[0]));
});

test("raw level reads delegate projection and filters to Parquet", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.readLevel("sample", {
    columns: ["internal:current_id", "ml:split"],
    filter: { "ml:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [{ "internal:current_id": 2n, "ml:split": "test" }]);
});

test("raw level reads select sparse physical row indexes", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.readLevel("sample", {
    columns: ["internal:current_id", "ml:split"],
    rowIndexes: [2, 0, 2],
  });
  assert.deepEqual(rows, [
    { "internal:current_id": 2n, "ml:split": "test" },
    { "internal:current_id": 0n, "ml:split": "train" },
    { "internal:current_id": 2n, "ml:split": "test" },
  ]);
  await assert.rejects(
    () => dataset.readLevel("sample", { rowIndexes: [3] }),
    /rowIndexes must be smaller/,
  );
  await assert.rejects(
    () => dataset.readLevel("sample", { rowIndexes: [0], rowStart: 0 }),
    /cannot be combined/,
  );
});

test("caches compressed metadata before projected queries", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const progress = [];
  await dataset.cacheLevel("sample", { onProgress: (event) => progress.push(event) });
  assert.equal(progress.at(-1).loaded, progress.at(-1).total);
  assert.ok(progress.at(-1).total > 0);
  const rows = await dataset.readLevel("sample", {
    columns: ["internal:current_id", "ml:split"],
    filter: { "ml:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [{ "internal:current_id": 2n, "ml:split": "test" }]);
  await assert.rejects(() => dataset.cacheLevel("missing"), /unknown metadata level/);
});

test("reads a metadata row count without decoding its rows", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  assert.equal(await dataset.levelRowCount("sample"), 3);
  assert.equal(await dataset.levelRowCount("children"), 6);
  await assert.rejects(() => dataset.levelRowCount("missing"), /unknown metadata level/);
});

test("reuses complete response buffers and copies partial views", () => {
  const bytes = new Uint8Array([1, 2, 3, 4]);
  assert.equal(arrayBuffer(bytes), bytes.buffer);
  const partial = bytes.subarray(1, 3);
  const copied = arrayBuffer(partial);
  assert.notEqual(copied, bytes.buffer);
  assert.deepEqual([...new Uint8Array(copied)], [2, 3]);
});

test("reads wide and long views with calculated TACO locations", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const wide = await dataset.read({ idx: 0 });
  assert.deepEqual(wide, [
    {
      "taco:sample_index": 0,
      id: "sample-0",
      "ml:split": "train",
      "image.bin::location": `/vsisubfile/225_64,/vsicurl/${fixture.baseUrl}/dataset.zip`,
      "mask.bin::location": `/vsisubfile/334_32,/vsicurl/${fixture.baseUrl}/dataset.zip`,
    },
  ]);

  const long = await dataset.read({ idx: 0, layout: "long" });
  assert.deepEqual(
    long.map((row) => [row["taco:sample_index"], row.path, row["file:role"], row["ml:split"]]),
    [
      [0, "image.bin", "image", "train"],
      [0, "mask.bin", "mask", "train"],
    ],
  );
  assert.deepEqual(Object.keys(long[0]).slice(0, 4), ["taco:sample_index", "id", "path", "taco:location"]);
  assert.match(long[0]["taco:location"], /^\/vsisubfile\/225_64,\/vsicurl\/http:/);
});

test("generated columns do not overwrite qualified metadata", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/collision`);
  const readLevel = dataset.readLevel.bind(dataset);
  dataset.readLevel = async (level, options) => {
    const rows = await readLevel(level, options);
    if (level === "sample") {
      return rows.map((row) => ({ ...row, "image_bin:location": "metadata-value" }));
    }
    if (level === "children") {
      return rows.map((row) => ({
        ...row,
        "internal:relative_path": row["internal:relative_path"].replace(/\/image\.bin$/, "/image_bin"),
      }));
    }
    return rows;
  };

  const [row] = await dataset.read({ idx: 0, files: ["image_bin"] });
  assert.equal(row["image_bin:location"], "metadata-value");
  assert.match(row["image_bin::location"], /^\/vsicurl\//);
});

test("supports idx, files, semantic filters, and location opt-out", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.read({
    idx: [0, 3],
    files: ["mask.bin"],
    location: false,
    filter: { "ml:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [
    { "taco:sample_index": 2, id: "sample-2", "ml:split": "test", "mask.bin::location": null },
  ]);

  const long = await dataset.read({ idx: [0, 2], layout: "long", files: ["image.bin"] });
  assert.deepEqual(long.map((row) => row.path), ["image.bin", "image.bin"]);
});

test("resolves a VSI location and fetches only the payload range", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const row = (await dataset.read({ idx: 0 }))[0];
  const asset = dataset.resolveAsset(row["image.bin::location"]);
  assert.equal(asset.url, `${fixture.baseUrl}/dataset.zip`);
  assert.equal(asset.offset, 225);
  assert.equal(asset.size, 64);
  const bytes = new Uint8Array(await asset.arrayBuffer());
  assert.deepEqual(bytes, fixture.entries.get("DATA/0/image.bin"));
  assert.ok(fixture.requests.some((request) => request.range === "bytes=225-288"));
});

test("opens a FOLDER without using ZIP offsets", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/folder`);
  assert.equal(dataset.container, "folder");
  const row = (await dataset.read({ idx: 1 }))[0];
  assert.equal(row["image.bin::location"], `/vsicurl/${fixture.baseUrl}/folder/DATA/1/image.bin`);
  const asset = dataset.resolveAsset(row["mask.bin::location"]);
  assert.equal(asset.offset, null);
  assert.equal(asset.size, null);
  assert.deepEqual(
    new Uint8Array(await asset.arrayBuffer()),
    fixture.entries.get("DATA/1/mask.bin"),
  );
});

test("finds a TACOCAT from its dataset root", async () => {
  const start = fixture.requests.length;
  const dataset = await openDataset(`${fixture.baseUrl}/catalog/`);

  assert.equal(dataset.container, "tacocat");
  assert.equal(dataset.url, `${fixture.baseUrl}/catalog/.tacocat/`);
  assert.deepEqual(
    fixture.requests.slice(start, start + 2).map((request) => request.path),
    ["/catalog/COLLECTION.json", "/catalog/.tacocat/COLLECTION.json"],
  );

  const explicit = await openDataset(`${fixture.baseUrl}/catalog/`, { container: "tacocat" });
  assert.equal(explicit.url, `${fixture.baseUrl}/catalog/.tacocat/`);
});

test("works when a server ignores byte ranges", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/full.zip`);
  const rows = await dataset.read({ idx: 2, files: ["mask.bin"] });
  assert.equal(rows[0]["mask.bin::location"], `/vsisubfile/708_32,/vsicurl/${fixture.baseUrl}/full.zip`);
});

test("top-level read() opens and materializes a source", async () => {
  const rows = await read(`${fixture.baseUrl}/dataset.zip`, { idx: 1, files: ["image.bin"] });
  assert.equal(rows.length, 1);
  assert.equal(rows[0]["taco:sample_index"], 1);
  assert.equal(rows[0]["ml:split"], "train");
});

test("rejects non-TACO profiles and corrupt archives", async () => {
  await assert.rejects(
    () => openDataset(`${fixture.baseUrl}/flat.zip`),
    (error) => error instanceof TacoError && error.code === "UNKNOWN_PROFILE",
  );
  await assert.rejects(
    () => openDataset(`${fixture.baseUrl}/corrupt.zip`),
    (error) => error instanceof TacoError && error.code === "HASH_MISMATCH",
  );
});

test("validates source and read options", async () => {
  await assert.rejects(() => openDataset("file:///tmp/dataset.zip"), /only http\(s\)/);
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  await assert.rejects(() => dataset.read({ layout: "rows" }), /layout must be wide or long/);
  await assert.rejects(() => dataset.read({ idx: [-1, 2] }), /idx must be an integer/);
  await assert.rejects(() => dataset.read({ files: ["unknown.bin"] }), /unknown structure leaves/);
  await assert.rejects(() => dataset.readLevel("missing"), /unknown metadata level/);
});

test("tasks is optional but cannot be empty", () => {
  const collection = {
    "taco:version": SUPPORTED_TACO_VERSION,
    id: "no-tasks",
    description: "No tasks",
    licenses: ["MIT"],
    providers: [{ name: "Asterisk Labs" }],
    "taco:structure": ["data.bin"],
    "taco:metadata": { sample: {}, children: {} },
  };
  assert.equal(parseCollection(collection).collection.tasks, undefined);
  assert.throws(() => parseCollection({ ...collection, tasks: [] }), /tasks must be a non-empty list/);
  assert.throws(() => parseCollection({ ...collection, "taco:structure": null }), /non-empty list/);
  assert.throws(() => parseCollection({ ...collection, "taco:structure": ["img*[0,3].tif"] }), /invalid bounds/);
});
