# TACO for JavaScript

Pure-JavaScript reader for remote [TACO](https://asterisk.coop/taco/spec/)
datasets. It opens FOLDER, ZIP, and TACOCAT containers directly over HTTP and
runs in modern browsers and Node.js 20 or newer.

It does not import, wrap, or depend on `@asterisk-labs/cozip`. ZIP support is a
private TACO-profile implementation
that validates CoZIP binary version 1, requires `profile = 2`, and verifies the
archive integrity hash before trusting priority-file offsets.

## Install

```bash
npm install @asterisk-labs/taco
```

## Open a dataset

```js
import { openDataset } from "@asterisk-labs/taco";

const dataset = await openDataset(
  "https://example.com/change-detection.zip",
);

console.log(dataset.container);  // "zip"
console.log(dataset.collection); // parsed COLLECTION.json
console.log(dataset.structure);  // taco:structure
console.log(dataset.levels);     // sample, children, ...
```

The same call accepts the URL of a FOLDER directory or a `.tacocat` directory.
Auto-detection treats `.zip` URLs as archives and classifies directories from
their `COLLECTION.json`. A container hint is available for URLs without the
standard suffix:

```js
const dataset = await openDataset(url, { container: "zip" });
```

## Read wide and long views

The default wide view contains one row per sample and one generated location
column per structure leaf.

```js
const samples = await dataset.read({
  layout: "wide",
  idx: [0, 20],
  files: ["before/image.rumi", "after/image.rumi"],
});
```

The long view contains one row per file. File metadata overrides metadata from
its ancestor folders, and folder metadata overrides sample metadata when the same
qualified field is declared at multiple levels.

```js
const files = await dataset.read({
  layout: "long",
  idx: 0,
});

console.log(files[0].path);
console.log(files[0]["taco:location"]);
```

`idx` is either one non-negative sample index or a half-open `[start, end]`
range. In TACOCAT it is applied separately to every source partition, so
`source_file` and `sample_id` together identify a sample.

Variable declarations such as `frame*[0,5].rumi` become ordered array columns
named by their prefix (`frame`). With `location: false`, every selected file
column is `null`; long rows retain a `taco:location` column whose values are
`null`.

A one-shot convenience function is also available:

```js
import { read } from "@asterisk-labs/taco";

const rows = await read(url, { layout: "wide", idx: [0, 10] });
```

## Read a raw metadata level

```js
const rows = await dataset.readLevel("children/before", {
  columns: ["internal:parent_id", "stac:time_start"],
  rowStart: 0,
  rowEnd: 100,
  filter: {
    "stac:time_start": { $gte: new Date("2024-01-01T00:00:00Z") },
  },
});
```

Raw reads expose the stored internal columns and never synthesize a location
column. Projection, row ranges, and filters are delegated to the Parquet
reader, including row-group pruning when statistics allow it.

The semantic `dataset.read({ filter })` form filters the completed wide or long
rows. It supports `$gt`, `$gte`, `$lt`, `$lte`, `$eq`, `$ne`, `$in`, `$nin`,
`$not`, `$and`, `$or`, and `$nor`.

Stored columns named `cozip:location` or `taco:location` are never trusted or
exposed. Raw reads remove them, long reads calculate `taco:location`, and wide
reads place calculated locations in the selected file columns.

## Read an asset in the browser

TACO ZIP locations use GDAL's VSI notation, which `fetch()` cannot consume
directly. `resolveAsset()` converts either a generated VSI location or a direct
FOLDER URL into an HTTP-readable object.

```js
const row = (await dataset.read({ idx: 0 }))[0];
const asset = dataset.resolveAsset(row["before/image.rumi"]);

console.log(asset.url);
console.log(asset.offset);
console.log(asset.size);

const bytes = await asset.arrayBuffer();
const blob = await asset.blob("application/octet-stream");
```

ZIP and TACOCAT assets are fetched with one HTTP byte-range request. FOLDER
assets are fetched from their direct URL.

The package treats payloads as opaque bytes. In particular, Rumi decoding does
not belong to TACO and is intentionally not included here.

## Authentication and CORS

Pass normal fetch options or a custom fetch implementation when required:

```js
const dataset = await openDataset(url, {
  requestInit: {
    headers: { Authorization: `Bearer ${token}` },
  },
});
```

Remote servers must permit cross-origin GET requests from the playground. ZIP
and Parquet objects should support `Range` and expose `Content-Range`; servers
that ignore `Range` still work, but the reader must download the complete
object.

## Scope of version 1

Version 1 is reader-only and supports TACO specification 3.0.0:

- HTTP(S) FOLDER, ZIP, and TACOCAT sources;
- authenticated CoZIP TACO-profile priority reads;
- Parquet projection, filters, and row slices;
- raw, wide, and long TACO views;
- fixed files, nested folders, and variable sequences;
- calculated locations and browser-native asset range reads.

It does not implement a writer, arbitrary SQL, local filesystem paths, or Rumi
decoding. The separate [fixture playground](https://github.com/asterisk-labs/taco/tree/main/deck/playground)
consumes this package directly and keeps payload decoding outside TACO.

## License

MIT. See [LICENSE](LICENSE).
