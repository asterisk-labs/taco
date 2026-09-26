# Language bindings

Sources: `python/`, `r/`, `julia/`, `javascript/`, `docs/playground/`, SPEC 8.

| Language | Install | Role |
| --- | --- | --- |
| Python | `pip install taco-eo` to read, `pip install 'taco-eo[writer]'` to write; imported as `taco` | read **and write** |
| R | `install.packages("taco", repos = "https://asterisk-labs.r-universe.dev")` | reader |
| Julia | `Pkg.Registry.add(url=".../AsteriskRegistry"); Pkg.add("Taco")` | reader |
| JavaScript | `npm install @asterisk-labs/taco` | reader |

Only Python writes. The specification allows readers, validators and inspection tools
in any language, but a library that creates datasets must not call itself a TACO
writer.

Python, R and Julia load the same `libtaco` and expose the same reader operations and
relations. Python additionally provides the writer, full physical validator,
consolidation and export. The JavaScript package is an independent reader built for
the browser and the playground, with its own query surface.

## Python

The distribution is `taco-eo`; the import name is `taco`, never `taco_eo`. Published
wheels bundle the native library. Building from the sdist needs a C++23 compiler,
CMake, Ninja, pkg-config, libcurl 7.83+ and OpenSSL 3+; the sdist force-includes
`core/` and `extern/karu/` under `native/`.

Runtime dependencies: `cffi`, `cozip`, `duckdb`, `numpy`, `tqdm`, `pyarrow`,
`pydantic`, `pyproj`, `shapely`. Extras: `rumi` (the Rumi extension), `geoenrich`
(Earth Engine), `test`, `dev`, `test-eo`.

The library is found at `taco/_lib/libtaco.{dylib,so}` or wherever `TACO_LIB` points.

## R

`r/` is a source package that **carries its own copy** of the TACO and Karu sources
and compiles them at install time, so it needs a C++23 compiler, pkg-config,
libcurl 7.83+ and OpenSSL 3+. Refresh the vendored copy with `make sync-r-core` after
touching `core/`; `tools/sync_r_core.py` does the copying. `make r` runs roxygen and
testthat.

Exports: `open_dataset`, `read`, `sql`, `inspect`, a `print` method for
`taco_dataset`, and progress through `cli`. `read` is generic over a character source
and a `taco_dataset`, and returns a tibble.

```r
dataset <- taco::open_dataset("dataset.zip")
samples <- taco::read(dataset)
train   <- taco::sql(dataset, 'SELECT * FROM dataset WHERE "ml:split" = \'train\'')
```

WebAssembly is not supported: the reader core needs DuckDB, libcurl and OpenSSL.

## Julia

`Taco.jl` downloads the matching native archive from the TACO GitHub release on first
use, resolved through `julia/Artifacts.toml`. Set `TACO_LIB` to use a development
build instead; `make julia` does exactly that after `make core`.

```julia
using Taco
dataset = Taco.open_dataset("dataset.zip")
samples = Taco.read(dataset)
```

`julia/src/reader/` mirrors the Python reader module for module: `native`, `source`,
`engine`, `collection`, `dataset`, `query`, `api`. Keep them aligned when the reader
surface changes.

Artifacts are why releasing takes two tags; see compatibility.md.

## JavaScript

`@asterisk-labs/taco` is a pure-JavaScript **reader** for remote datasets over HTTP,
for modern browsers and Node.js 20+. It implements ZIP itself rather than depending on
`@asterisk-labs/cozip`, validates CoZIP binary version 1 and profile 2, and checks the
archive hash before trusting priority offsets.

```js
import { openDataset, read } from "@asterisk-labs/taco";

const dataset = await openDataset("https://example.com/change-detection.zip");
dataset.container;   // "zip" | "folder" | "tacocat"
dataset.collection;  // parsed COLLECTION.json
dataset.structure;
dataset.levels;

const samples = await dataset.read({ layout: "wide", idx: [0, 20],
                                     files: ["before/image.rumi"] });
const files   = await dataset.read({ layout: "long", idx: 0 });
const rows    = await dataset.readLevel("children/before", {
  columns: ["internal:parent_id", "stac:datetime"],
  rowStart: 0, rowEnd: 100,
  filter: { "stac:datetime": { $gte: new Date("2024-01-01T00:00:00Z") } },
});
```

Differences from the native readers:

- **No SQL.** Filtering is a Mongo-style object supporting `$gt`, `$gte`, `$lt`,
  `$lte`, `$eq`, `$ne`, `$in`, `$nin`, `$not`, `$and`, `$or`, `$nor`. `readLevel`
  pushes projection, row ranges and filters into the Parquet reader, including
  row-group pruning; `read({ filter })` filters completed rows.
- **Both layouts are public.** `layout: "wide"` and `layout: "long"` are first-class,
  where Python exposes long only through `native_sql`.
- **No local paths and no writer.** HTTP(S) sources only.
- **`resolveAsset`** converts a VSI location or a FOLDER URL into something `fetch`
  can use: `{ url, offset, size, arrayBuffer(), blob() }`. ZIP and TACOCAT assets are
  one byte-range request.
- Auth and CORS go through `requestInit` or a custom `fetch`. Servers should support
  `Range` and expose `Content-Range`; one that ignores `Range` still works but forces
  a full download.
- Payloads stay opaque bytes; Rumi decoding is a Rumi library's job.

`docs/playground/` is the dataset viewer built on this package, deployed with the
site. `make javascript` runs `npm ci`, the type build, tests and a pack dry run.

## Keeping bindings aligned

A change to the reader surface touches five places: the core (if the SQL or options
change), then the Python, R and Julia wrappers, then the JavaScript implementation,
then `SPEC.md` 8.2 and 8.3. The specification is normative: if a binding and the spec
disagree, the binding is wrong. Record the change in `CHANGELOG.md`, which covers the
core and every package together.
