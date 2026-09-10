import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { openDataset, read, SUPPORTED_TACO_VERSION, TacoError } from "../src/index.js";
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

test("reads wide and long views with calculated TACO locations", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const wide = await dataset.read({ idx: 0 });
  assert.deepEqual(wide, [
    {
      sample_id: 0,
      "ml:split": "train",
      "image.bin": `/vsisubfile/225_64,/vsicurl/${fixture.baseUrl}/dataset.zip`,
      "mask.bin": `/vsisubfile/334_32,/vsicurl/${fixture.baseUrl}/dataset.zip`,
    },
  ]);

  const long = await dataset.read({ idx: 0, layout: "long" });
  assert.deepEqual(
    long.map((row) => [row.sample_id, row.path, row["file:role"], row["ml:split"]]),
    [
      [0, "image.bin", "image", "train"],
      [0, "mask.bin", "mask", "train"],
    ],
  );
  assert.match(long[0]["taco:location"], /^\/vsisubfile\/225_64,\/vsicurl\/http:/);
});

test("supports idx, files, semantic filters, and location opt-out", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.read({
    idx: [0, 3],
    files: ["mask.bin"],
    location: false,
    filter: { "ml:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [{ sample_id: 2, "ml:split": "test", "mask.bin": null }]);

  const long = await dataset.read({ idx: [0, 2], layout: "long", files: ["image.bin"] });
  assert.deepEqual(long.map((row) => row.path), ["image.bin", "image.bin"]);
});

test("resolves a VSI location and fetches only the payload range", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const row = (await dataset.read({ idx: 0 }))[0];
  const asset = dataset.resolveAsset(row["image.bin"]);
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
  assert.equal(row["image.bin"], `${fixture.baseUrl}/folder/DATA/1/image.bin`);
  const asset = dataset.resolveAsset(row["mask.bin"]);
  assert.equal(asset.offset, null);
  assert.equal(asset.size, null);
  assert.deepEqual(
    new Uint8Array(await asset.arrayBuffer()),
    fixture.entries.get("DATA/1/mask.bin"),
  );
});

test("works when a server ignores byte ranges", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/full.zip`);
  const rows = await dataset.read({ idx: 2, files: ["mask.bin"] });
  assert.equal(rows[0]["mask.bin"], `/vsisubfile/708_32,/vsicurl/${fixture.baseUrl}/full.zip`);
});

test("top-level read() opens and materializes a source", async () => {
  const rows = await read(`${fixture.baseUrl}/dataset.zip`, { idx: 1, files: ["image.bin"] });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].sample_id, 1);
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
