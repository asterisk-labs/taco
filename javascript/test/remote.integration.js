import test from "node:test";
import assert from "node:assert/strict";
import { openDataset } from "../src/index.js";

const ROOT = "https://huggingface.co/datasets/asterisk-labs/taco-api-fixtures/resolve/main";

test("opens all 50 published fixtures", { timeout: 180_000 }, async () => {
  const response = await fetch(`${ROOT}/manifest.json`);
  assert.equal(response.ok, true);
  const manifest = await response.json();
  assert.equal(manifest.datasets.length, 50);

  for (let start = 0; start < manifest.datasets.length; start += 5) {
    const batch = manifest.datasets.slice(start, start + 5);
    await Promise.all(
      batch.map(async (entry) => {
        const dataset = await openDataset(`${ROOT}/${entry.path}`);
        const rows = await dataset.read({ idx: 0, location: false });
        assert.ok(rows.length > 0, entry.id);
        assert.equal(dataset.collection.id, entry.case);
      }),
    );
  }
});

test("wide reads carry the Rumi header next to each location", { timeout: 120_000 }, async () => {
  const variable = await openDataset(`${ROOT}/data/06-variable-sequence/single-zip/dataset.zip`);
  const [row] = await variable.read({ idx: 0 });
  const sequence = Object.keys(row).find((name) => name.endsWith("::location") && Array.isArray(row[name]));
  assert.ok(sequence, "the variable fixture has a sequence column");
  const headers = row[sequence.replace(/::location$/, "::header")];
  assert.equal(headers.length, row[sequence].length);
  assert.ok(headers.every((header) => header instanceof Uint8Array));

  const nested = await openDataset(`${ROOT}/data/04-change-detection/folder`);
  const [first] = await nested.read({ idx: 0 });
  const pairs = Object.keys(first).filter((name) => name.endsWith("::header"));
  assert.ok(pairs.some((name) => name.includes("/")), "nested headers keep their structure path");
  for (const name of pairs) {
    assert.ok(first[name] instanceof Uint8Array);
    assert.equal(typeof first[name.replace(/::header$/, "::location")], "string");
  }
});

test("reads nested, variable, and TACOCAT locations and fetches Rumi", { timeout: 120_000 }, async () => {
  const cases = [
    "data/04-change-detection/folder",
    "data/06-variable-sequence/single-zip/dataset.zip",
    "data/08-nested-fusion/by-split/.tacocat",
  ];
  for (const path of cases) {
    const dataset = await openDataset(`${ROOT}/${path}`);
    const rows = await dataset.read({ idx: 0, layout: "long" });
    assert.ok(rows.length > 0);
    assert.ok(rows[0]["rumi:header"] instanceof Uint8Array);
    const asset = dataset.resolveAsset(rows[0]["taco:location"]);
    const bytes = new Uint8Array(await asset.arrayBuffer());
    assert.ok(
      ["LOVE", "RUMI"].includes(new TextDecoder().decode(bytes.subarray(0, 4))),
      `${path} did not return a Rumi payload`,
    );
  }
});
