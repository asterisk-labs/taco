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
