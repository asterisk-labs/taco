const DEFAULT_MAX_SIDE = 1024;
const MAX_STRETCH_SAMPLES = 50_000;

function dimensionInfo(info) {
  if (!Array.isArray(info.shape) || ![3, 4].includes(info.shape.length)) {
    throw new Error("Rumi preview needs a 3D or 4D raster");
  }
  const width = info.shape.at(-1);
  const height = info.shape.at(-2);
  const bands = info.shape.at(-3);
  if (![width, height, bands].every((value) => Number.isSafeInteger(value) && value > 0)) {
    throw new Error("Rumi preview has invalid dimensions");
  }
  return { width, height, bands };
}

function supportedDtype(dtype) {
  const name = dtype?.name ?? "";
  return !name.startsWith("complex")
    && !name.startsWith("float8")
    && !["float16", "bfloat16"].includes(name);
}

function rgbBands(labels, count) {
  if (!Array.isArray(labels)) return [0, 1, 2];
  const patterns = [
    [/^(?:SR_)?B0?4(?![0-9A-Za-z])/i, /^red$/i],
    [/^(?:SR_)?B0?3(?![0-9A-Za-z])/i, /^green$/i],
    [/^(?:SR_)?B0?2(?![0-9A-Za-z])/i, /^blue$/i],
  ];
  const matches = patterns.map((names) => labels.findIndex(
    (label) => names.some((pattern) => pattern.test(label)),
  ));
  return matches.every((index) => index >= 0 && index < count)
    && new Set(matches).size === 3
    ? matches
    : [0, 1, 2];
}

export function previewRequest(info, maxSide = DEFAULT_MAX_SIDE) {
  if (!Number.isSafeInteger(maxSide) || maxSide <= 0) {
    throw new RangeError("preview size must be a positive integer");
  }
  if (!supportedDtype(info.dtype)) {
    throw new Error(`The viewer cannot render ${info.dtype?.name ?? "this data type"}.`);
  }
  const source = dimensionInfo(info);
  const width = Math.min(source.width, maxSide);
  const height = Math.min(source.height, maxSide);
  const x = Math.floor((source.width - width) / 2);
  const y = Math.floor((source.height - height) / 2);
  const labels = info.bands;
  const bands = source.bands < 3
    ? [0]
    : rgbBands(labels, source.bands);
  return {
    bands,
    time: [0],
    window: [y, x, height, width],
    width,
    height,
    cropped: width !== source.width || height !== source.height,
    sourceWidth: source.width,
    sourceHeight: source.height,
  };
}

export function previewFrameCount(header, request) {
  const [y, x, height, width] = request.window;
  const tilesAcross = Math.ceil((x % header.tileWidth + width) / header.tileWidth);
  const tilesDown = Math.ceil((y % header.tileHeight + height) / header.tileHeight);
  const bandFrames = header.frameAxes.includes("band") ? 1 : request.bands.length;
  const timeFrames = header.frameAxes.includes("time") ? 1 : request.time.length;
  return tilesAcross * tilesDown * bandFrames * timeFrames;
}

export function previewDecodedBytes(header, request) {
  const [y, x, height, width] = request.window;
  const ty0 = Math.floor(y / header.tileHeight);
  const tx0 = Math.floor(x / header.tileWidth);
  const ty1 = Math.ceil((y + height) / header.tileHeight);
  const tx1 = Math.ceil((x + width) / header.tileWidth);
  let tilePixels = 0;
  for (let ty = ty0; ty < ty1; ty += 1) {
    const tileHeight = Math.min(header.tileHeight, header.height - ty * header.tileHeight);
    for (let tx = tx0; tx < tx1; tx += 1) {
      const tileWidth = Math.min(header.tileWidth, header.width - tx * header.tileWidth);
      tilePixels += tileWidth * tileHeight;
    }
  }
  const bands = header.frameAxes.includes("band") ? header.bands : request.bands.length;
  const times = header.frameAxes.includes("time") ? header.timeCount : request.time.length;
  const total = tilePixels * bands * times * header.dtype.bytes;
  if (!Number.isSafeInteger(total)) throw new RangeError("Rumi preview decode size is too large");
  return total;
}

export function fitPreviewRequest(header, request, maxDecodedBytes) {
  if (!Number.isSafeInteger(maxDecodedBytes) || maxDecodedBytes <= 0) {
    throw new RangeError("preview decode limit must be a positive safe integer");
  }
  let fitted = { ...request, window: [...request.window] };
  while (previewDecodedBytes(header, fitted) > maxDecodedBytes) {
    const [y, x, height, width] = fitted.window;
    if (height === 1 && width === 1) return null;
    const tileRows = Math.ceil((y % header.tileHeight + height) / header.tileHeight);
    const tileColumns = Math.ceil((x % header.tileWidth + width) / header.tileWidth);
    const shrinkWidth = width > 1 && (height === 1 || tileColumns >= tileRows);
    const nextWidth = shrinkWidth ? Math.ceil(width / 2) : width;
    const nextHeight = shrinkWidth ? height : Math.ceil(height / 2);
    fitted = {
      ...fitted,
      window: [
        y + Math.floor((height - nextHeight) / 2),
        x + Math.floor((width - nextWidth) / 2),
        nextHeight,
        nextWidth,
      ],
      width: nextWidth,
      height: nextHeight,
      cropped: true,
    };
  }
  return fitted;
}

export function tacoAssetSource(asset, signal, maxFullBytes) {
  const options = { signal, maxFullBytes };
  let sizePromise;
  let lastYield = performance.now();
  let yieldPromise = null;
  const prepare = () => {
    sizePromise ??= (async () => {
      const size = await asset.byteLength(options);
      if (Number.isSafeInteger(asset.offset) && size > 0) {
        await asset.read(0, 1, options);
      }
      return size;
    })();
    return sizePromise;
  };
  const yieldForAbort = async () => {
    signal?.throwIfAborted();
    if (!yieldPromise && performance.now() - lastYield >= 16) {
      let shared;
      shared = new Promise((resolve) => setTimeout(resolve, 0)).finally(() => {
        if (yieldPromise === shared) {
          lastYield = performance.now();
          yieldPromise = null;
        }
      });
      yieldPromise = shared;
    }
    if (yieldPromise) await yieldPromise;
    signal?.throwIfAborted();
  };
  return {
    async size() {
      return BigInt(await prepare());
    },
    async read(offset, length) {
      if (typeof offset !== "bigint" || offset < 0n || offset > BigInt(Number.MAX_SAFE_INTEGER)) {
        throw new RangeError("Rumi offset is outside JavaScript's safe integer range");
      }
      await prepare();
      await yieldForAbort();
      return asset.read(Number(offset), length, options);
    },
  };
}

function limits(data, offset, pixels) {
  const step = Math.max(1, Math.ceil(pixels / MAX_STRETCH_SAMPLES));
  const sample = [];
  const bigInt = typeof data[offset] === "bigint";
  for (let index = 0; index < pixels; index += step) {
    const value = data[offset + index];
    if (bigInt || Number.isFinite(value)) sample.push(value);
  }
  if (sample.length === 0) return [0, 1];
  sample.sort(bigInt ? (a, b) => (a < b ? -1 : a > b ? 1 : 0) : (a, b) => a - b);
  const low = sample[Math.floor((sample.length - 1) * 0.02)];
  const high = sample[Math.ceil((sample.length - 1) * 0.98)];
  if (low !== high) return [low, high];
  return bigInt ? [low - 1n, high + 1n] : [low - 0.5, high + 0.5];
}

function byte(value, low, high) {
  if (typeof value === "bigint") {
    if (high <= low) return 128;
    return Math.round(Math.max(0, Math.min(1, Number(value - low) / Number(high - low))) * 255);
  }
  if (!Number.isFinite(value)) return 0;
  if (!(high > low)) return 128;
  return Math.round(Math.max(0, Math.min(1, (value - low) / (high - low))) * 255);
}

export function rasterRgba(data, width, height, bands) {
  if (bands !== 1 && bands !== 3) throw new Error("Rumi preview needs one or three bands");
  const pixels = width * height;
  if (data.length !== pixels * bands) throw new Error("Rumi preview data has the wrong size");
  const stretches = Array.from({ length: bands }, (_, band) => limits(data, band * pixels, pixels));
  const rgba = new Uint8ClampedArray(pixels * 4);

  for (let pixel = 0; pixel < pixels; pixel += 1) {
    const target = pixel * 4;
    if (bands === 1) {
      const sample = data[pixel];
      const value = byte(sample, ...stretches[0]);
      rgba[target] = value;
      rgba[target + 1] = value;
      rgba[target + 2] = value;
      rgba[target + 3] = typeof sample === "bigint" || Number.isFinite(sample) ? 255 : 0;
    } else {
      let valid = true;
      for (let channel = 0; channel < 3; channel += 1) {
        const sample = data[channel * pixels + pixel];
        rgba[target + channel] = byte(sample, ...stretches[channel]);
        valid &&= typeof sample === "bigint" || Number.isFinite(sample);
      }
      rgba[target + 3] = valid ? 255 : 0;
    }
  }
  return rgba;
}

export function affineWindowCorners(transform, window, pixelIsPoint = false) {
  if (!Array.isArray(transform) || transform.length !== 6) {
    throw new Error("Rumi transform must contain six values");
  }
  const [y, x, height, width] = window;
  const shift = pixelIsPoint ? -0.5 : 0;
  const x0 = x + shift;
  const y0 = y + shift;
  const x1 = x + width + shift;
  const y1 = y + height + shift;
  const [a, b, c, d, e, f] = transform;
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(
    ([column, row]) => [a * column + b * row + c, d * column + e * row + f],
  );
}

export function mapCorners(corners) {
  if (!Array.isArray(corners) || corners.length !== 4) return null;
  const limit = 85.051129;
  const longitudes = [];
  for (const corner of corners) {
    if (!Array.isArray(corner) || corner.length !== 2 || !corner.every(Number.isFinite)) return null;
    longitudes.push(corner[0]);
  }
  const west = Math.min(...longitudes);
  const east = Math.max(...longitudes);
  if (east - west >= 180) return null;

  const firstWorld = Math.ceil((-180 - west) / 360);
  const lastWorld = Math.floor((180 - east) / 360);
  if (firstWorld > lastWorld) return null;
  const centeredWorld = Math.round(-(west + east) / 720);
  const world = Math.max(firstWorld, Math.min(lastWorld, centeredWorld));
  const shift = world * 360;
  const result = corners.map(([longitude, latitude]) => [
    longitude + shift,
    Math.max(-limit, Math.min(limit, latitude)),
  ]);

  let area = 0;
  for (let index = 0; index < result.length; index += 1) {
    const [x0, y0] = result[index];
    const [x1, y1] = result[(index + 1) % result.length];
    area += x0 * y1 - x1 * y0;
  }
  if (Math.abs(area) <= 1e-12) return null;
  return result;
}

export function supportsMemory64() {
  return typeof WebAssembly !== "undefined" && WebAssembly.validate(
    Uint8Array.of(0, 97, 115, 109, 1, 0, 0, 0, 5, 3, 1, 4, 0),
  );
}
