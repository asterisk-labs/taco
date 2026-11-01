import test from "node:test";
import assert from "node:assert/strict";

import {
  affineWindowCorners,
  fitPreviewRequest,
  mapCorners,
  previewDecodedBytes,
  previewFrameCount,
  previewRequest,
  rasterRgba,
  tacoAssetSource,
} from "../../docs/playground/rumi-preview.js";

function info(shape, bands = null, dtype = "uint16") {
  return { shape, bands, dtype: { name: dtype } };
}

test("selects Sentinel-2 RGB labels and a bounded center window", () => {
  const request = previewRequest(info([1, 4, 3000, 4000], ["B02", "B03", "B04", "B08"]));
  assert.deepEqual(request.bands, [2, 1, 0]);
  assert.deepEqual(request.time, [0]);
  assert.deepEqual(request.window, [988, 1488, 1024, 1024]);
  assert.equal(request.cropped, true);
});

test("recognizes Sentinel-2 band labels with resolution suffixes", () => {
  const request = previewRequest(info(
    [4, 20, 30],
    ["B02_10m", "B03_10m", "B04_10m", "B08_10m"],
  ));
  assert.deepEqual(request.bands, [2, 1, 0]);
});

test("recognizes Landsat surface reflectance labels", () => {
  const request = previewRequest(info(
    [4, 20, 30],
    ["SR_B2", "SR_B3", "SR_B4", "SR_B5"],
  ));
  assert.deepEqual(request.bands, [2, 1, 0]);
});

test("falls back to the first three bands when RGB labels are incomplete", () => {
  const request = previewRequest(info([3, 20, 30], ["nir", "B04", "swir"]));
  assert.deepEqual(request.bands, [0, 1, 2]);
});

test("renders one band for two-band Rumi images", () => {
  const request = previewRequest(info([2, 20, 30], ["red", "nir"]));
  assert.deepEqual(request.bands, [0]);
  assert.deepEqual(request.window, [0, 0, 20, 30]);
  assert.equal(request.cropped, false);
});

test("counts frame ranges before materializing a large preview plan", () => {
  const request = { window: [0, 0, 1024, 1024], bands: [0, 1, 2], time: [0] };
  assert.equal(
    previewFrameCount({ tileWidth: 16, tileHeight: 16, frameAxes: ["band"] }, request),
    4096,
  );
  assert.equal(
    previewFrameCount({ tileWidth: 16, tileHeight: 16, frameAxes: [] }, request),
    12_288,
  );
  assert.equal(
    previewFrameCount(
      { tileWidth: 256, tileHeight: 256, frameAxes: ["band", "time"] },
      { ...request, window: [250, 250, 20, 20] },
    ),
    4,
  );
});

test("counts all bytes decoded for a preview", () => {
  const request = { window: [0, 0, 1024, 1024], bands: [4, 3, 2], time: [0] };
  assert.equal(previewDecodedBytes({
    width: 1024,
    height: 1024,
    tileWidth: 16,
    tileHeight: 16,
    bands: 32_768,
    timeCount: 1,
    frameAxes: ["band"],
    dtype: { bytes: 8 },
  }, request), 256 * 1024 ** 3);
  assert.equal(previewDecodedBytes({
    width: 25,
    height: 20,
    tileWidth: 16,
    tileHeight: 16,
    bands: 4,
    timeCount: 1,
    frameAxes: [],
    dtype: { bytes: 2 },
  }, { ...request, window: [15, 15, 5, 10] }), 3_000);
});

test("shrinks a preview to its decoded-byte budget", () => {
  const header = {
    width: 1024,
    height: 1024,
    tileWidth: 256,
    tileHeight: 256,
    bands: 13,
    timeCount: 10,
    frameAxes: ["band", "time"],
    dtype: { bytes: 2 },
  };
  const request = previewRequest(info([10, 13, 1024, 1024]));
  const fitted = fitPreviewRequest(header, request, 256 * 1024 * 1024);
  assert.ok(fitted);
  assert.equal(fitted.cropped, true);
  assert.ok(previewDecodedBytes(header, fitted) <= 256 * 1024 * 1024);

  const hugeTile = { ...header, width: 256, height: 256, bands: 4096, timeCount: 1, dtype: { bytes: 8 } };
  assert.equal(
    fitPreviewRequest(hugeTile, previewRequest(info([4096, 256, 256])), 256 * 1024 * 1024),
    null,
  );
});

test("rejects data types the canvas preview cannot interpret", () => {
  assert.throws(() => previewRequest(info([1, 2, 2], null, "float16")), /cannot render/);
  assert.throws(() => previewRequest(info([1, 2, 2], null, "complex64")), /cannot render/);
});

test("places a cropped PixelIsPoint window at pixel boundaries", () => {
  assert.deepEqual(
    affineWindowCorners([10, 0, 100, 0, -10, 200], [2, 3, 4, 5], true),
    [[125, 185], [175, 185], [175, 145], [125, 145]],
  );
});

test("clips map corners to the Web Mercator latitude domain", () => {
  assert.deepEqual(mapCorners([
    [-10, 90.125], [10, 90.125], [10, -90.125], [-10, -90.125],
  ]), [
    [-10, 85.051129], [10, 85.051129], [10, -85.051129], [-10, -85.051129],
  ]);
  assert.equal(mapCorners([[0, 0], [1, 0], [1, NaN], [0, 1]]), null);
});

test("normalizes other world copies and rejects unsafe map quads", () => {
  assert.deepEqual(mapCorners([
    [200, 20], [250, 20], [250, 10], [200, 10],
  ]), [
    [-160, 20], [-110, 20], [-110, 10], [-160, 10],
  ]);
  assert.equal(mapCorners([
    [170, 20], [190, 20], [190, 10], [170, 10],
  ]), null);
  assert.equal(mapCorners([
    [-180, 80], [180, 80], [180, -80], [-180, -80],
  ]), null);
  assert.equal(mapCorners([
    [-10, 89], [10, 89], [10, 86], [-10, 86],
  ]), null);
});

test("stretches BigInt samples and makes invalid pixels transparent", () => {
  const rgba = rasterRgba(new BigInt64Array([0n, 10n, 20n, 30n]), 2, 2, 1);
  assert.deepEqual([...rgba.filter((_, index) => index % 4 === 3)], [255, 255, 255, 255]);
  const missing = rasterRgba(new Float32Array([0, NaN]), 2, 1, 1);
  assert.equal(missing[7], 0);
  const large = rasterRgba(new BigUint64Array([2n ** 63n, 2n ** 63n]), 2, 1, 1);
  assert.deepEqual([...large], [128, 128, 128, 255, 128, 128, 128, 255]);
  const base = 2n ** 63n;
  const precise = rasterRgba(new BigUint64Array([base, base + 1n, base + 2n, base + 3n]), 4, 1, 1);
  assert.deepEqual([precise[0], precise[4], precise[8], precise[12]], [0, 85, 170, 255]);
});

test("adapts TacoAsset offsets to the Rumi source contract", async () => {
  const calls = [];
  const asset = {
    async byteLength() { return 20; },
    async read(offset, length, options) {
      calls.push({ offset, length, signal: options.signal });
      return new Uint8Array(length).fill(offset);
    },
  };
  const controller = new AbortController();
  const source = tacoAssetSource(asset, controller.signal);
  assert.equal(await source.size(), 20n);
  assert.deepEqual(await source.read(4n, 3), new Uint8Array([4, 4, 4]));
  assert.deepEqual(calls, [{ offset: 4, length: 3, signal: controller.signal }]);
  await assert.rejects(() => source.read(BigInt(Number.MAX_SAFE_INTEGER) + 1n, 1), /safe integer/);
});

test("probes a nested asset before Rumi starts parallel reads", async () => {
  const calls = [];
  const asset = {
    offset: 100,
    async byteLength(options) {
      calls.push({ method: "size", options });
      return 20;
    },
    async read(offset, length, options) {
      calls.push({ method: "read", offset, length, options });
      return new Uint8Array(length);
    },
  };
  const source = tacoAssetSource(asset, undefined, 64);
  assert.equal(await source.size(), 20n);
  await source.read(4n, 3);
  assert.deepEqual(calls.map(({ method, offset, length }) => ({ method, offset, length })), [
    { method: "size", offset: undefined, length: undefined },
    { method: "read", offset: 0, length: 1 },
    { method: "read", offset: 4, length: 3 },
  ]);
  assert.ok(calls.every(({ options }) => options.maxFullBytes === 64));
});

test("cached Rumi reads yield so an abort can run", async () => {
  const controller = new AbortController();
  const asset = {
    offset: null,
    async byteLength() { return 20; },
    async read(_offset, length) {
      const end = performance.now() + 20;
      while (performance.now() < end) {}
      return new Uint8Array(length);
    },
  };
  const source = tacoAssetSource(asset, controller.signal);
  setTimeout(() => controller.abort(), 0);
  await assert.rejects(async () => {
    for (let index = 0; index < 2; index += 1) await source.read(0n, 1);
  }, /aborted/i);
});
