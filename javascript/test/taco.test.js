import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { Dataset, openDataset, read, SUPPORTED_TACO_VERSION, TacoError } from "../src/index.js";
import { TacoAsset } from "../src/container/asset.js";
import { openTacoArchive } from "../src/container/cozip.js";
import { arrayBuffer, HttpClient, openHttpObject } from "../src/container/http.js";
import { parseCollection } from "../src/contract/collection.js";
import { parseCollectionJson } from "../src/reader/collection.js";
import { matchesFilter } from "../src/reader/filter.js";
import { openSource } from "../src/reader/source.js";
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
    columns: ["internal:current_id", "split:split", "taco:location", "cozip:location"],
    rowStart: 0,
    rowEnd: 2,
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(Object.keys(rows[0]).sort(), ["internal:current_id", "split:split"].sort());
  assert.ok(!("taco:location" in rows[0]));
  assert.ok(!("cozip:location" in rows[0]));
});

test("raw level reads delegate projection and filters to Parquet", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.readLevel("sample", {
    columns: ["internal:current_id", "split:split"],
    filter: { "split:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [{ "internal:current_id": 2n, "split:split": "test" }]);
});

test("raw level row ranges select stored rows before the filter", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const filter = { "split:split": "test" };
  const ids = (rows) => rows.map((row) => row["internal:current_id"]);
  assert.deepEqual(ids(await dataset.readLevel("sample", { rowStart: 2, rowEnd: 3, filter })), [2n]);
  assert.deepEqual(ids(await dataset.readLevel("sample", { rowStart: 0, rowEnd: 2, filter })), []);
  assert.deepEqual(await dataset.read({ level: "sample", idx: 0, filter }), []);
  assert.deepEqual(ids(await dataset.read({ level: "sample", idx: [1, 3], filter })), [2n]);
});

test("raw level reads select sparse physical row indexes", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.readLevel("sample", {
    columns: ["internal:current_id", "split:split"],
    rowIndexes: [2, 0, 2],
  });
  assert.deepEqual(rows, [
    { "internal:current_id": 2n, "split:split": "test" },
    { "internal:current_id": 0n, "split:split": "train" },
    { "internal:current_id": 2n, "split:split": "test" },
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
    columns: ["internal:current_id", "split:split"],
    filter: { "split:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [{ "internal:current_id": 2n, "split:split": "test" }]);
  await assert.rejects(() => dataset.cacheLevel("missing"), /unknown metadata level/);
});

test("caching a metadata level returns its Parquet bytes", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const bytes = await dataset.cacheLevel("sample");
  assert.ok(bytes instanceof ArrayBuffer);
  assert.deepEqual(new Uint8Array(bytes), fixture.entries.get("METADATA/sample.parquet"));
  assert.equal(await dataset.cacheLevel("sample"), bytes);
});

test("a failed level read rejects its caller and is retried", async () => {
  const unhandled = [];
  const record = (reason) => unhandled.push(reason);
  process.on("unhandledRejection", record);
  try {
    const level = "/folder/METADATA/sample.parquet";
    const busy = (error) => error instanceof TacoError && error.code === "HTTP_ERROR" && /HTTP 503/.test(error.message);

    // The size probe of a FOLDER level fails once.
    const probe = await openDataset(`${fixture.baseUrl}/folder`, {
      fetch: failOnce((url, range) => url.endsWith(level) && range === "bytes=0-0"),
    });
    await assert.rejects(() => probe.read({ location: false }), busy);
    assert.equal((await probe.read({ location: false })).length, 3);

    // The footer fails once.
    const footer = await openDataset(`${fixture.baseUrl}/folder`, {
      fetch: failOnce((url, range) => url.endsWith(level) && range !== "bytes=0-0"),
    });
    await assert.rejects(() => footer.levelRowCount("sample"), busy);
    assert.equal(await footer.levelRowCount("sample"), 3);

    // The whole download fails once.
    const download = await openDataset(`${fixture.baseUrl}/folder`, {
      fetch: failOnce((url, range) => url.endsWith(level) && range !== "bytes=0-0"),
    });
    await assert.rejects(() => download.cacheLevel("sample"), busy);
    const bytes = await download.cacheLevel("sample");
    assert.deepEqual(new Uint8Array(bytes), fixture.entries.get("METADATA/sample.parquet"));

    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", record);
  }
});

test("caching a level checks the level name stored in its footer", async () => {
  const unhandled = [];
  const record = (reason) => unhandled.push(reason);
  process.on("unhandledRejection", record);
  try {
    const swapped = async (url, init) => fetch(String(url).replace("/children.parquet", "/sample.parquet"), init);
    const dataset = await openDataset(`${fixture.baseUrl}/folder`, { fetch: swapped });
    await assert.rejects(
      () => dataset.cacheLevel("children"),
      (error) => error instanceof TacoError && error.code === "INVALID_PARQUET_LEVEL",
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", record);
  }
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
      "split:split": "train",
      "image.bin::location": `/vsisubfile/225_64,/vsicurl/${fixture.baseUrl}/dataset.zip`,
      "mask.bin::location": `/vsisubfile/334_32,/vsicurl/${fixture.baseUrl}/dataset.zip`,
    },
  ]);

  const long = await dataset.read({ idx: 0, layout: "long" });
  assert.deepEqual(
    long.map((row) => [row["taco:sample_index"], row.path, row["file:role"], row["split:split"]]),
    [
      [0, "image.bin", "image", "train"],
      [0, "mask.bin", "mask", "train"],
    ],
  );
  assert.deepEqual(Object.keys(long[0]).slice(0, 4), ["taco:sample_index", "id", "path", "taco:location"]);
  assert.match(long[0]["taco:location"], /^\/vsisubfile\/225_64,\/vsicurl\/http:/);
});

test("wide columns keep the structure path of nested files", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/nested.zip`);
  const [row] = await dataset.read({ idx: 0 });
  assert.match(row["before/B02.bin::location"], /^\/vsisubfile\//);
  assert.match(row["after/B02.bin::location"], /^\/vsisubfile\//);
  assert.match(row["change.bin::location"], /^\/vsisubfile\//);
  assert.ok(!Object.keys(row).some((name) => name.includes("__")));
});

test("long rows follow the samples and the structure inside each sample", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/nested.zip`);
  const rows = await dataset.read({ layout: "long", location: false, idx: [1, 3] });
  assert.deepEqual(rows.map((row) => `${row["taco:sample_index"]}:${row.path}`), [
    "1:before/B02.bin",
    "1:before/B03.bin",
    "1:after/B02.bin",
    "1:change.bin",
    "2:before/B02.bin",
    "2:before/B03.bin",
    "2:after/B02.bin",
    "2:change.bin",
  ]);
});

test("a variable sequence only takes indexes below its maximum", async () => {
  // img*[1,3].bin ends at img2.bin, so img10.bin is a fixed file.
  const url = `${fixture.baseUrl}/dataset.zip`;
  const client = new HttpClient();
  const archive = await openTacoArchive(url, client);
  const collection = JSON.parse(new TextDecoder().decode(await archive.read("COLLECTION.json")));
  collection["taco:structure"] = ["img*[1,3].bin", "img10.bin"];
  const dataset = new Dataset(await openSource(url, client, "zip", parseCollection(collection)), client);
  const readLevel = dataset.readLevel.bind(dataset);
  dataset.readLevel = async (level, options) => (await readLevel(level, options)).map((row) => (
    level === "children"
      ? {
          ...row,
          "internal:relative_path": row["internal:relative_path"]
            .replace("/image.bin", "/img0.bin")
            .replace("/mask.bin", "/img10.bin"),
        }
      : row
  ));

  const [row] = await dataset.read({ idx: 0 });
  assert.deepEqual(row["img::location"], [`/vsisubfile/225_64,/vsicurl/${url}`]);
  assert.equal(row["img10.bin::location"], `/vsisubfile/334_32,/vsicurl/${url}`);
});

test("wide reads tolerate folder-only levels without payload columns", async () => {
  // Older archives may omit offsets on a level containing only folders.
  const url = `${fixture.baseUrl}/nested.zip`;
  const client = new HttpClient();
  const archive = await openTacoArchive(url, client);
  const collection = JSON.parse(new TextDecoder().decode(await archive.read("COLLECTION.json")));
  collection["taco:structure"] = collection["taco:structure"].filter((path) => path !== "change.bin");
  const dataset = new Dataset(await openSource(url, client, "zip", parseCollection(collection)), client);
  // Simulate hyparquet's error when those columns are requested.
  const readLevel = dataset.readLevel.bind(dataset);
  dataset.readLevel = async (level, options = {}) => {
    const payload = ["internal:offset", "internal:size"];
    const missing = payload.find((name) => options.columns?.includes(name));
    if (level === "children" && missing) throw new Error(`parquet column not found: ${missing}`);
    return readLevel(level, options);
  };

  const [row] = await dataset.read({ idx: 0 });
  assert.match(row["before/B02.bin::location"], /^\/vsisubfile\/738_240,/);
  assert.match(row["after/B02.bin::location"], /^\/vsisubfile\/1319_240,/);
  assert.ok(!("change.bin::location" in row));
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

test("readers ignore a stored taco:sample_index", async () => {
  // Store it in place of raster:resolution, which has the same length.
  const archive = await openTacoArchive(`${fixture.baseUrl}/nested.zip`, new HttpClient());
  const files = new Map([["/nested/COLLECTION.json", await archive.read("COLLECTION.json")]]);
  for (const name of ["sample", "children", "children__before", "children__after"]) {
    const bytes = Buffer.from(await archive.read(`METADATA/${name}.parquet`));
    if (name === "children__before") renameAll(bytes, "raster:resolution", "taco:sample_index");
    files.set(`/nested/METADATA/${name}.parquet`, bytes);
  }
  const dataset = await openDataset("https://example.test/nested/", { fetch: staticFetch(files).fetch });

  const rows = await dataset.readLevel("children/before");
  assert.equal(rows.length, 6);
  assert.ok(rows.every((row) => !("taco:sample_index" in row)));
  await assert.rejects(
    () => dataset.readLevel("children/before", { filter: { "taco:sample_index": 0n } }),
    (error) => error instanceof TacoError && error.code === "PROTECTED_COLUMN",
  );
  const long = await dataset.read({ layout: "long", location: false, files: ["before/B02.bin"] });
  assert.deepEqual(long.map((row) => row["taco:sample_index"]), [0, 1, 2]);

  // A wide row keeps the generated index even when a sample row carries one.
  const readLevel = dataset.readLevel.bind(dataset);
  dataset.readLevel = async (level, options) => (await readLevel(level, options))
    .map((row) => (level === "sample" ? { ...row, "taco:sample_index": 100n } : row));
  assert.deepEqual((await dataset.read({ location: false })).map((row) => row["taco:sample_index"]), [0, 1, 2]);
});

test("wide reads carry Rumi headers and statistics next to locations", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/rumi.zip`);
  const [row] = await dataset.read({ idx: 0 });
  assert.deepEqual(Object.keys(row).filter((name) => name.includes("::")), [
    "scene/image.rumi::location",
    "scene/image.rumi::header",
    "scene/image.rumi::mean",
    "scene/series::location",
    "scene/series::header",
    "scene/series::maximum_b1",
    "mask.bin::location",
  ]);
  assert.equal(new TextDecoder().decode(row["scene/image.rumi::header"].subarray(0, 4)), "LOVE");
  assert.equal(row["scene/image.rumi::mean"], 15.5);
  // Sequences follow the numeric index, not the order the files were written.
  assert.deepEqual(row["scene/series::maximum_b1"], [51, 41]);

  const [quiet] = await dataset.read({ idx: 0, files: ["scene/image.rumi"], location: false });
  assert.deepEqual(quiet, {
    "taco:sample_index": 0,
    id: "rumi-0",
    "scene/image.rumi::location": null,
    "scene/image.rumi::header": null,
    "scene/image.rumi::mean": null,
  });
});

test("supports idx, files, semantic filters, and location opt-out", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const rows = await dataset.read({
    idx: [0, 3],
    files: ["mask.bin"],
    location: false,
    filter: { "split:split": { $eq: "test" } },
  });
  assert.deepEqual(rows, [
    { "taco:sample_index": 2, id: "sample-2", "split:split": "test", "mask.bin::location": null },
  ]);

  const long = await dataset.read({ idx: [0, 2], layout: "long", files: ["image.bin"] });
  assert.deepEqual(long.map((row) => row.path), ["image.bin", "image.bin"]);
});

test("read and readLevel treat null values alike", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/rumi.zip`);
  // children/scene stores rumi:mean for image.rumi and null for the series files.
  const read = async (filter) => (await dataset.read({
    layout: "long",
    location: false,
    files: ["scene/image.rumi", "scene/series*[1,2].rumi"],
    filter,
  })).map((row) => `${row["taco:sample_index"]}/${row.path}`).sort();
  const readLevel = async (filter) => (await dataset.readLevel("children/scene", {
    columns: ["internal:relative_path"],
    filter,
  })).map((row) => row["internal:relative_path"]).sort();
  const images = ["0/scene/image.rumi", "1/scene/image.rumi"];
  const series = ["0/scene/series0.rumi", "0/scene/series1.rumi", "1/scene/series0.rumi", "1/scene/series1.rumi"];
  const notFirst = [...series, "1/scene/image.rumi"].sort();
  for (const [filter, expected] of [
    [{ "rumi:mean": { $lt: 100 } }, ["0/scene/image.rumi"]],
    [{ "rumi:mean": { $lte: 15.5 } }, ["0/scene/image.rumi"]],
    [{ "rumi:mean": { $gte: 0 } }, images],
    [{ "rumi:mean": null }, series],
    [{ "rumi:mean": { $eq: null } }, series],
    [{ "rumi:mean": { $ne: null } }, images],
    [{ "rumi:mean": { $in: [null, 115.5] } }, notFirst],
    [{ "rumi:mean": { $ne: 15.5 } }, notFirst],
    [{ "rumi:mean": { $nin: [15.5] } }, notFirst],
    [{ "rumi:mean": { $not: { $lt: 100 } } }, notFirst],
    [{ $nor: [{ "rumi:mean": { $lt: 100 } }] }, notFirst],
  ]) {
    assert.deepEqual(await read(filter), expected, JSON.stringify(filter));
    assert.deepEqual(await readLevel(filter), expected, JSON.stringify(filter));
  }

  // mask.bin rows of the long view have no rumi:mean, and null matches them.
  const missing = await dataset.read({ layout: "long", location: false, filter: { "rumi:mean": null } });
  assert.deepEqual(missing.filter((row) => row.path === "mask.bin").length, 2);
  const present = await dataset.read({ layout: "long", location: false, filter: { "rumi:mean": { $gte: 0 } } });
  assert.deepEqual(present.map((row) => row.path), ["scene/image.rumi", "scene/image.rumi"]);
});

test("read and readLevel compare Dates by time and binary values by content", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/nested.zip`);
  const day = new Date("2024-01-02T00:00:00Z");
  const read = async (filter) => (await dataset.read({ location: false, filter })).map((row) => row.id);
  const readLevel = async (filter) => (await dataset.readLevel("sample", { columns: ["id"], filter }))
    .map((row) => row.id);
  for (const ids of [read, readLevel]) {
    assert.deepEqual(await ids({ "geo:time_start": day }), ["nested-1"]);
    assert.deepEqual(await ids({ "geo:time_start": { $eq: new Date(day) } }), ["nested-1"]);
    assert.deepEqual(await ids({ "geo:time_start": { $in: [new Date(day)] } }), ["nested-1"]);
    assert.deepEqual(await ids({ "geo:time_start": { $ne: day } }), ["nested-0", "nested-2"]);
    assert.deepEqual(await ids({ "geo:time_start": { $not: day } }), ["nested-0", "nested-2"]);
    assert.deepEqual(await ids({ "geo:time_start": { $gt: day } }), ["nested-2"]);
  }
  const [{ "geo:centroid": centroid }] = await dataset.readLevel("sample", {
    columns: ["geo:centroid"],
    rowStart: 1,
    rowEnd: 2,
  });
  assert.deepEqual(await read({ "geo:centroid": centroid.slice() }), ["nested-1"]);
  assert.deepEqual(await readLevel({ "geo:centroid": centroid.slice() }), ["nested-1"]);
});

test("read filters compare arrays by item and find columns named after files", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/rumi.zip`);
  const ids = async (filter) => (await dataset.read({ filter })).map((row) => row.id);
  assert.deepEqual(await ids({ "scene/series::maximum_b1": [51, 41] }), ["rumi-0"]);
  assert.deepEqual(await ids({ "scene/series::maximum_b1": { $in: [[151, 141]] } }), ["rumi-1"]);
  assert.deepEqual(await ids({ "scene/series::maximum_b1": { $in: [141, 7] } }), ["rumi-1"]);
  assert.deepEqual(await ids({ "scene/series::maximum_b1": { $nin: [51] } }), ["rumi-1"]);
  assert.deepEqual(await ids({ "scene/series::maximum_b1": { $ne: [51, 41] } }), ["rumi-1"]);
  assert.deepEqual(await ids({ "scene/image.rumi::mean": { $gt: 100 } }), ["rumi-1"]);
  assert.deepEqual(await ids({ "scene/image.rumi::mean": 15.5 }), ["rumi-0"]);
});

test("readLevel filters stored columns whose names contain a dot", async () => {
  // hyparquet alone would look for field "it" of a column "split:sp".
  const files = new Map();
  for (const [name, bytes] of fixture.entries) {
    if (name.startsWith("DATA/")) continue;
    const renamed = Buffer.from(bytes);
    renameAll(renamed, "split:split", "split:sp.it");
    files.set(`/dotted/${name}`, renamed);
  }
  const dataset = await openDataset("https://example.test/dotted/", { fetch: staticFetch(files).fetch });
  const ids = async (options) => (await dataset.readLevel("sample", options)).map((row) => row["internal:current_id"]);

  assert.deepEqual(
    await dataset.readLevel("sample", { columns: ["id"], filter: { "split:sp.it": "test" } }),
    [{ id: "sample-2" }],
  );
  assert.deepEqual(await ids({ rowStart: 1, rowEnd: 3, filter: { "split:sp.it": { $ne: "test" } } }), [1n]);
  assert.deepEqual(await ids({ filter: { $or: [{ "split:sp.it": "test" }, { id: "sample-0" }] } }), [0n, 2n]);
  assert.deepEqual(
    (await dataset.read({ location: false, filter: { "split:sp.it": "test" } })).map((row) => row.id),
    ["sample-2"],
  );
});

test("filters compare structs by field and resolve dotted paths", () => {
  const row = { "spatial:centroid": { lon: 1, lat: 2 } };
  assert.equal(matchesFilter(row, { "spatial:centroid": { $eq: { lat: 2, lon: 1 } } }), true);
  assert.equal(matchesFilter(row, { "spatial:centroid": { $eq: { lon: 1 } } }), false);
  assert.equal(matchesFilter(row, { "spatial:centroid.lat": { $not: { $gt: 1 } } }), false);
  assert.equal(matchesFilter(row, { "spatial:centroid.lat": { $not: { $gt: 2 } } }), true);
  assert.equal(matchesFilter(row, { "spatial:centroid.height": { $gt: 2 } }), false);
  assert.equal(matchesFilter(row, { "spatial:centroid.height": { $not: { $gt: 2 } } }), true);
  assert.equal(matchesFilter(row, { "spatial:centroid.height": null }), true);
  assert.equal(matchesFilter(row, { "spatial:centroid": null }), false);
  assert.throws(
    () => matchesFilter(row, { "spatial:centroid": { $not: { $near: 1 } } }),
    (error) => error instanceof TacoError && error.code === "INVALID_FILTER",
  );
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

test("reads part of an asset without downloading the complete payload", async () => {
  const dataset = await openDataset(`${fixture.baseUrl}/dataset.zip`);
  const [row] = await dataset.read({ idx: 0 });
  const asset = dataset.resolveAsset(row["image.bin::location"]);
  const before = fixture.requests.length;
  assert.equal(await asset.byteLength(), 64);
  assert.deepEqual(
    await asset.read(7, 5),
    fixture.entries.get("DATA/0/image.bin").subarray(7, 12),
  );
  assert.equal(fixture.requests[before].range, "bytes=232-236");
  await assert.rejects(() => asset.read(63, 2), /invalid asset range/);
});

test("rejects an asset that changes between range requests", async () => {
  let request = 0;
  const fetch = async (_url, init) => {
    const offset = request++;
    assert.equal(init.headers.get("range"), `bytes=${offset}-${offset}`);
    assert.equal(init.headers.get("if-match"), null);
    return new Response(Uint8Array.of(offset), {
      status: 206,
      headers: {
        "content-range": `bytes ${offset}-${offset}/4`,
        ...(offset === 0 ? { etag: '"first"' } : {}),
      },
    });
  };
  const asset = new TacoAsset(
    "/vsisubfile/0_4,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
  );
  assert.deepEqual(await asset.read(0, 1), Uint8Array.of(0));
  await assert.rejects(
    () => asset.read(1, 1),
    (error) => error instanceof TacoError && error.code === "SOURCE_CHANGED",
  );
});

test("rejects an asset when its archive changed after opening", async () => {
  const fetch = async () => new Response(Uint8Array.of(0), {
    status: 206,
    headers: { "content-range": "bytes 0-0/4", etag: '"second"' },
  });
  const asset = new TacoAsset(
    "/vsisubfile/0_4,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
    { sourceSize: 4, etag: '"first"' },
  );
  await assert.rejects(
    () => asset.read(0, 1),
    (error) => error instanceof TacoError && error.code === "SOURCE_CHANGED",
  );
});

test("rejects an object when its ETag disappears", async () => {
  let request = 0;
  const fetch = async (_url, init) => {
    const range = init.headers.get("range");
    const [start, end] = range.slice(6).split("-").map(Number);
    return new Response(new Uint8Array(end - start + 1), {
      status: 206,
      headers: {
        "content-range": `bytes ${start}-${end}/2`,
        ...(request++ === 0 ? { etag: '"first"' } : {}),
      },
    });
  };
  const { object } = await openHttpObject(
    new HttpClient({ fetch }),
    "https://example.test/data.zip",
  );
  await assert.rejects(
    () => object.sliceBytes(1, 2),
    (error) => error instanceof TacoError && error.code === "SOURCE_CHANGED",
  );
});

test("does not expose cached asset bytes to callers", async () => {
  const source = Uint8Array.from({ length: 32 }, (_, index) => index);
  const fetch = async () => new Response(source, {
    status: 200,
    headers: { "content-length": String(source.byteLength) },
  });
  const asset = new TacoAsset(
    "/vsisubfile/8_8,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
  );
  const first = await asset.read(0, 4);
  first.fill(255);
  assert.deepEqual(await asset.read(0, 4), source.slice(8, 12));
  const whole = new Uint8Array(await asset.arrayBuffer());
  whole.fill(0);
  assert.deepEqual(await asset.read(0, 4), source.slice(8, 12));
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(() => asset.read(0, 1, { signal: controller.signal }), /aborted/i);
});

test("bounds full responses when a server ignores Range", async () => {
  const fetch = async () => new Response(new Uint8Array(32), {
    status: 200,
    headers: { "content-length": "32" },
  });
  const asset = new TacoAsset(
    "/vsisubfile/0_8,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
  );
  await assert.rejects(
    () => asset.read(0, 1, { maxFullBytes: 16 }),
    (error) => error instanceof TacoError && error.code === "FULL_RESPONSE_TOO_LARGE",
  );
});

test("reads full responses without a usable Content-Length", async () => {
  const source = Uint8Array.from({ length: 32 }, (_, index) => index);
  for (const headers of [{}, { "content-length": "8", "content-encoding": "gzip" }]) {
    const client = new HttpClient({
      fetch: async () => new Response(source, { status: 200, headers }),
    });
    const result = await client.range(
      "https://example.test/data.zip",
      8,
      15,
      undefined,
      { maxFullBytes: 64 },
    );
    assert.deepEqual(result.bytes, source.slice(8, 16));
    assert.equal(result.totalSize, source.byteLength);
  }
});

test("keeps range reads parallel after the server answers with 206", async () => {
  let active = 0;
  let peak = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const fetch = async (_url, init) => {
    const [start, end] = init.headers.get("range").slice(6).split("-").map(Number);
    active += 1;
    peak = Math.max(peak, active);
    if (start !== 0) await gate;
    active -= 1;
    return new Response(new Uint8Array(end - start + 1), {
      status: 206,
      headers: { "content-range": `bytes ${start}-${end}/16` },
    });
  };
  const asset = new TacoAsset(
    "/vsisubfile/0_16,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
  );
  await asset.read(0, 1);
  const reads = Array.from({ length: 8 }, (_, index) => asset.read(index + 1, 1));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(peak, 8);
  release();
  await Promise.all(reads);
});

test("shares the first full response between concurrent asset reads", async () => {
  const source = Uint8Array.from({ length: 32 }, (_, index) => index);
  let requests = 0;
  const fetch = async () => {
    requests += 1;
    return new Response(source, {
      status: 200,
      headers: { "content-length": String(source.byteLength) },
    });
  };
  const asset = new TacoAsset(
    "/vsisubfile/0_8,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
  );
  const reads = Array.from(
    { length: 8 },
    (_, offset) => asset.read(offset, 1, { maxFullBytes: offset === 1 ? 16 : 64 }),
  );
  const results = await Promise.allSettled(reads);
  assert.deepEqual(
    results.map((result) => result.status),
    ["fulfilled", "rejected", "fulfilled", "fulfilled", "fulfilled", "fulfilled", "fulfilled", "fulfilled"],
  );
  assert.equal(results[1].reason.code, "FULL_RESPONSE_TOO_LARGE");
  assert.equal(requests, 1);
});

test("a waiting asset read keeps its own abort signal", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const fetch = async (_url, init) => {
    await gate;
    const range = init.headers.get("range");
    const [start, end] = range.slice(6).split("-").map(Number);
    return new Response(new Uint8Array(end - start + 1), {
      status: 206,
      headers: { "content-range": `bytes ${start}-${end}/8` },
    });
  };
  const asset = new TacoAsset(
    "/vsisubfile/0_8,/vsicurl/https://example.test/data.zip",
    new HttpClient({ fetch }),
  );
  const first = asset.read(0, 1);
  const controller = new AbortController();
  const waiting = asset.read(1, 1, { signal: controller.signal });
  controller.abort();
  await assert.rejects(waiting, /aborted/i);
  release();
  await first;
});

test("combines request and per-read abort signals", async () => {
  const shared = new AbortController();
  const local = new AbortController();
  shared.abort();
  const fetch = async (_url, init) => {
    assert.equal(init.signal.aborted, true);
    throw init.signal.reason;
  };
  const client = new HttpClient({ fetch, requestInit: { signal: shared.signal } });
  await assert.rejects(
    () => client.range("https://example.test/data", 0, 0, undefined, { signal: local.signal }),
    /aborted/i,
  );
});

test("stops reading an oversized partial response", async () => {
  const fetch = async () => new Response(Uint8Array.of(1, 2), {
    status: 206,
    headers: { "content-range": "bytes 0-0/2" },
  });
  const client = new HttpClient({ fetch });
  await assert.rejects(() => client.range("https://example.test/data", 0, 0), /exceeds/);
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

test("percent-encodes level names in FOLDER and TACOCAT URLs", async () => {
  // Rename the nested fixture's before/ folder to b#%41e. Unencoded, "#"
  // starts a fragment and a server reads "%41" as "A".
  const archive = await openTacoArchive(`${fixture.baseUrl}/nested.zip`, new HttpClient());
  const collection = JSON.parse(
    new TextDecoder().decode(await archive.read("COLLECTION.json")).replaceAll("before", "b#%41e"),
  );
  const renamed = Buffer.from(await archive.read("METADATA/children__before.parquet"));
  renamed.write("children/b#%41e", renamed.indexOf("children/before"), "latin1");
  /** @type {Record<string, Uint8Array>} */
  const levels = {
    "sample.parquet": await archive.read("METADATA/sample.parquet"),
    "children.parquet": await archive.read("METADATA/children.parquet"),
    "children__b#%41e.parquet": renamed,
    "children__after.parquet": await archive.read("METADATA/children__after.parquet"),
  };
  const catalog = {
    ...collection,
    "taco:sources": { samples: 3, partitions: [{ file: "part.zip", samples: 3 }] },
  };
  const files = new Map([
    ["/my data/COLLECTION.json", new TextEncoder().encode(JSON.stringify(collection))],
    ["/catalog/.tacocat/COLLECTION.json", new TextEncoder().encode(JSON.stringify(catalog))],
    ...Object.entries(levels).flatMap(([name, bytes]) => [
      [`/my data/METADATA/${name}`, bytes],
      [`/catalog/.tacocat/${name}`, bytes],
    ]),
  ]);
  const { fetch, requests } = staticFetch(files);

  // The base URL is used as given, so its %20 is not encoded again.
  const folder = await openDataset("https://example.test/my%20data/", { fetch });
  assert.equal(await folder.levelRowCount("children/b#%41e"), 6);
  assert.ok(requests.includes("https://example.test/my%20data/METADATA/children__b%23%2541e.parquet"));

  const tacocat = await openDataset("https://example.test/catalog/.tacocat/", { fetch });
  const rows = await tacocat.readLevel("children/b#%41e", { columns: ["internal:parent_id"] });
  assert.equal(rows.length, 6);
  assert.ok(requests.includes("https://example.test/catalog/.tacocat/children__b%23%2541e.parquet"));
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
  const before = fixture.requests.length;
  const dataset = await openDataset(`${fixture.baseUrl}/full.zip`);
  const rows = await dataset.read({ idx: 2, files: ["mask.bin"] });
  assert.equal(rows[0]["mask.bin::location"], `/vsisubfile/708_32,/vsicurl/${fixture.baseUrl}/full.zip`);
  const asset = dataset.resolveAsset(rows[0]["mask.bin::location"]);
  assert.equal((await asset.read(0, 1))[0], fixture.entries.get("DATA/2/mask.bin")[0]);
  assert.equal(
    fixture.requests.slice(before).filter(({ path }) => path === "/full.zip").length,
    1,
  );
});

test("top-level read() opens and materializes a source", async () => {
  const rows = await read(`${fixture.baseUrl}/dataset.zip`, { idx: 1, files: ["image.bin"] });
  assert.equal(rows.length, 1);
  assert.equal(rows[0]["taco:sample_index"], 1);
  assert.equal(rows[0]["split:split"], "train");
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
  const legacyRumi = {
    ...collection,
    "taco:metadata": {
      ...collection["taco:metadata"],
      children: {
        "rumi:stats": { type: "list<double>", nullable: true, description: "Legacy statistics" },
      },
    },
  };
  assert.throws(() => parseCollection(legacyRumi), /invalid Rumi field "rumi:stats"/);
});

test("validates per-file Rumi statistics", () => {
  const field = (name, files) => ({
    "taco:version": SUPPORTED_TACO_VERSION,
    id: "rumi-files",
    description: "Rumi file scopes",
    licenses: ["MIT"],
    providers: [{ name: "Asterisk Labs" }],
    "taco:structure": ["data.rumi"],
    "taco:metadata": {
      sample: {},
      children: {
        [name]: {
          type: name === "rumi:header" ? "binary" : "double",
          nullable: true,
          description: "",
          files,
        },
      },
    },
  });

  assert.throws(() => parseCollection(field("rumi:mean", "data.rumi")), /non-empty list/);
  assert.throws(() => parseCollection(field("rumi:mean", [])), /non-empty list/);
  assert.throws(() => parseCollection(field("rumi:mean", ["data.rumi", "data.rumi"])), /duplicates/);
  assert.throws(() => parseCollection(field("rumi:header", ["data.rumi"])), /cannot be restricted/);
  assert.throws(() => parseCollection(field("quality:score", ["data.rumi"])), /cannot be restricted/);
  assert.throws(() => parseCollection(field("rumi:mean", ["missing.rumi"])), /unknown file/);
});

test("rejects the collections that the Python reader rejects", () => {
  const collection = {
    "taco:version": SUPPORTED_TACO_VERSION,
    id: "rules",
    description: "Collection rules",
    licenses: ["MIT"],
    providers: [{ name: "Asterisk Labs" }],
    "taco:structure": ["data.bin"],
    "taco:metadata": { sample: {}, children: {} },
  };
  const field = { type: "string", nullable: true, description: "" };
  for (const [change, code, message] of [
    [{ "taco:bogus": 1 }, "INVALID_COLLECTION", /unknown reserved keys: taco:bogus/],
    [{ "internal:x": 1 }, "INVALID_COLLECTION", /unknown reserved keys: internal:x/],
    [{ notes: "x" }, "INVALID_COLLECTION", /unknown unqualified field "notes"/],
    [{ "Source:mission": "S2" }, "INVALID_COLLECTION", /invalid collection metadata field/],
    [{ id: "a/b" }, "INVALID_COLLECTION", /id must not contain/],
    [{ description: " \t" }, "INVALID_COLLECTION", /description must be a non-empty string/],
    [{ licenses: ["MIT", ""] }, "INVALID_COLLECTION", /licenses must be a non-empty list of non-empty strings/],
    [{ title: 7 }, "INVALID_COLLECTION", /title must be a string/],
    [{ keywords: "eo" }, "INVALID_COLLECTION", /keywords must be a list/],
    [{ "taco:extensions": ["a", "a"] }, "INVALID_COLLECTION", /must not repeat/],
    [{ providers: ["Asterisk Labs"] }, "INVALID_COLLECTION", /providers\[0\] must be an object/],
    [{ providers: [{ name: "x", roles: "producer" }] }, "INVALID_COLLECTION", /providers\[0\]\.roles/],
    [{ providers: [{ name: "x", url: "ftp://example.com" }] }, "INVALID_COLLECTION", /url must start with/],
    [{ curators: [{ email: "team@example.com" }] }, "INVALID_COLLECTION", /name or an organization/],
    [{ extent: { spatial: [0, 5, 1, 1] } }, "INVALID_COLLECTION", /outside EPSG:4326/],
    [{ extent: { spatial: [0, 0, 1, 1], temporal: "2024" } }, "INVALID_COLLECTION", /extent\.temporal/],
    [
      { "taco:metadata": { sample: { "info:name": field, "info:Name": field }, children: {} } },
      "INVALID_CONTRACT",
      /"info:name" and "info:Name" at sample differ only in case/,
    ],
    [
      { "taco:metadata": { sample: { "info:a\u0000b": field }, children: {} } },
      "INVALID_CONTRACT",
      /invalid qualified field/,
    ],
  ]) {
    assert.throws(
      () => parseCollection({ ...collection, ...change }),
      (error) => error instanceof TacoError && error.code === code && message.test(error.message),
      JSON.stringify(change),
    );
  }
  // As in Python, null stands for an optional field that is not there.
  const empty = { tasks: null, title: null, curators: null, keywords: null, extent: null, "taco:extensions": null };
  assert.equal(parseCollection({ ...collection, ...empty }).collection.tasks, null);
  parseCollection({ ...collection, "source:mission": "S2", extent: { spatial: [170, -5, -170, 5], temporal: null } });
});

test("rejects JSON that the native reader rejects and JSON.parse accepts", () => {
  const text = JSON.stringify({
    "taco:version": SUPPORTED_TACO_VERSION,
    id: "json",
    description: "JSON limits",
    licenses: ["MIT"],
    providers: [{ name: "Asterisk Labs" }],
    "taco:structure": ["data.bin"],
    "taco:metadata": { sample: {}, children: {} },
  });
  const parse = (body) => parseCollectionJson(new TextEncoder().encode(body), "COLLECTION.json");
  const withValue = (value) => `${text.slice(0, -1)},"x:y":${value}}`;
  // The document is at depth 0, so the innermost 1 is at depth 256.
  parse(withValue(`${"[".repeat(255)}1${"]".repeat(255)}`));
  parse(withValue('"\\ud83d\\ude00"'));
  for (const [body, message] of [
    [withValue(`${"[".repeat(256)}1${"]".repeat(256)}`), /nests deeper than 256 levels/],
    [withValue("1e400"), /not finite/],
    [withValue('"\\ud800"'), /lone surrogate/],
    [`\ufeff${text}`, /could not parse/],
  ]) {
    assert.throws(
      () => parse(body),
      (error) => error instanceof TacoError && error.code === "INVALID_COLLECTION" && message.test(error.message),
    );
  }
});

test("rejects structures whose entries readers cannot tell apart", () => {
  const collection = (structure) => {
    const levels = ["sample", "children"];
    for (const path of structure) {
      const folders = path.split("/").slice(0, -1);
      folders.forEach((_, depth) => levels.push(`children/${folders.slice(0, depth + 1).join("/")}`));
    }
    return {
      "taco:version": SUPPORTED_TACO_VERSION,
      id: "structure",
      description: "Structure rules",
      licenses: ["MIT"],
      providers: [{ name: "Asterisk Labs" }],
      "taco:structure": structure,
      "taco:metadata": Object.fromEntries(levels.map((level) => [level, {}])),
    };
  };
  for (const [structure, message] of [
    [["a*[1,2]/b.tif"], /reserved sequence characters/],
    [["a", "a/b.tif"], /children under the sample root must have distinct identifiers/],
    [["B02.tif", "b02.tif"], /"B02.tif" and "b02.tif" under the sample root differ only in case/],
    [["img*[1,3].tif", "IMG2.TIF"], /"IMG2.TIF" overlaps "img\*\[1,3\]\.tif"/],
    [["img*[1,3]", "img1/b.tif"], /"img1" overlaps/],
    [["a/img*[1,20].tif", "a/img1*[1,3].tif"], /overlaps "a\/img1\*\[1,3\]\.tif" under a$/],
    [["a_/b/x.tif", "a/_b/y.tif"], /both map to METADATA\/children__a___b\.parquet$/],
    [["A_/b/x.tif", "a/_b/y.tif"], /on a case-insensitive file system$/],
    [["img*[ 1,3].tif"], /malformed variable leaf/],
    [["img*[1, 3].tif"], /malformed variable leaf/],
    [["img*[01,3].tif"], /malformed variable leaf/],
    [["img*[+1,3].tif"], /malformed variable leaf/],
    [["img*[1,9007199254740992].tif"], /invalid bounds/],
  ]) {
    assert.throws(
      () => parseCollection(collection(structure)),
      (error) => error instanceof TacoError && message.test(error.message),
      structure.join(" "),
    );
  }
  // A sequence produces img0.tif to img2.tif, and its prefix need not be a valid name.
  for (const structure of [
    ["img*[1,3].tif", "img10.tif", "img02.tif"],
    ["img*[1,10].tif", "img1*[1,3].tif"],
    ["img.*[1,3].tif", "img *[1,9007199254740991].tif"],
  ]) {
    parseCollection(collection(structure));
  }
});

/**
 * Answer the first request that matches with 503, then pass every request on.
 *
 * @param {(url: string, range: string | null) => boolean} matches
 */
function failOnce(matches) {
  let failed = false;
  return async (url, init) => {
    if (!failed && matches(String(url), new Headers(init?.headers).get("range"))) {
      failed = true;
      return new Response("busy", { status: 503, statusText: "Service Unavailable" });
    }
    return fetch(url, init);
  };
}

/**
 * Replace every occurrence of a name with another of the same length, so a
 * Parquet footer keeps its layout.
 *
 * @param {Buffer} bytes
 * @param {string} from
 * @param {string} to
 */
function renameAll(bytes, from, to) {
  assert.equal(from.length, to.length);
  for (let at = bytes.indexOf(from); at >= 0; at = bytes.indexOf(from, at)) bytes.write(to, at, "latin1");
}

/**
 * Serve files by decoded path, as a static file server does, and record the
 * requested URLs.
 *
 * @param {Map<string, Uint8Array>} files
 */
function staticFetch(files) {
  /** @type {string[]} */
  const requests = [];
  const fetch = async (url, init) => {
    requests.push(String(url));
    const bytes = files.get(decodeURIComponent(new URL(url).pathname));
    if (!bytes) return new Response("not found", { status: 404, statusText: "Not Found" });
    const range = new Headers(init?.headers).get("range")?.match(/^bytes=(\d+)-(\d+)$/);
    if (!range) return new Response(bytes, { status: 200 });
    const start = Number(range[1]);
    const end = Math.min(Number(range[2]), bytes.length - 1);
    return new Response(bytes.subarray(start, end + 1), {
      status: 206,
      headers: { "content-range": `bytes ${start}-${end}/${bytes.length}` },
    });
  };
  return { fetch, requests };
}
