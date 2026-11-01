import test from "node:test";
import assert from "node:assert/strict";
import { openRumi } from "@asterisk-labs/rumi";

import { tacoAssetSource } from "../../docs/playground/rumi-preview.js";
import { openDataset } from "../src/index.js";
import { fixtureServer } from "./server.js";

test("decodes a Rumi window through nested TACO ranges", async (context) => {
  const fixture = await fixtureServer();
  context.after(() => fixture.close());
  const dataset = await openDataset(`${fixture.baseUrl}/rumi.zip`);
  const [row] = await dataset.read({ idx: 0 });
  const asset = dataset.resolveAsset(row["scene/image.rumi::location"]);
  const before = fixture.requests.length;
  const rumi = await openRumi(tacoAssetSource(asset), {
    maxFrameBytes: 1024,
    maxMetadataBytes: 1024,
    maxOutputBytes: 1024,
  });
  const result = await rumi.read({ bands: [0, 1], window: [1, 1, 2, 2] });
  assert.deepEqual(result.shape, [2, 2, 2]);
  assert.deepEqual([...result.data], [5, 6, 9, 10, 21, 22, 25, 26]);

  const requests = fixture.requests.slice(before).filter(({ path }) => path === "/rumi.zip");
  assert.ok(requests.length > 1);
  assert.ok(requests.every(({ range }) => {
    const match = /^bytes=(\d+)-(\d+)$/.exec(range ?? "");
    return match && Number(match[1]) >= asset.offset && Number(match[2]) < asset.offset + asset.size;
  }));
  assert.ok(!requests.some(({ range }) => range === `bytes=${asset.offset}-${asset.offset + asset.size - 1}`));
});

test("decodes a Rumi window from a TACO folder", async (context) => {
  const fixture = await fixtureServer();
  context.after(() => fixture.close());
  const dataset = await openDataset(`${fixture.baseUrl}/rumi-folder`);
  const [row] = await dataset.read({ idx: 0 });
  const asset = dataset.resolveAsset(row["scene/image.rumi::location"]);
  const before = fixture.requests.length;
  const rumi = await openRumi(tacoAssetSource(asset), {
    maxFrameBytes: 1024,
    maxMetadataBytes: 1024,
    maxOutputBytes: 1024,
  });
  const result = await rumi.read({ bands: [0], window: [1, 1, 2, 2] });
  assert.deepEqual([...result.data], [5, 6, 9, 10]);
  const requests = fixture.requests.slice(before)
    .filter(({ path }) => path === "/rumi-folder/DATA/0/scene/image.rumi");
  assert.equal(requests[0].range, "bytes=0-0");
  assert.ok(requests.length > 1);
  assert.ok(requests.every(({ range }) => /^bytes=\d+-\d+$/.test(range ?? "")));
});
