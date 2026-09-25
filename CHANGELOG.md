# Changelog

All notable changes across the TACO core, language bindings, writer, and
JavaScript reader are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- `taco.extensions.Rumi(header=False)` stores `rumi:stats` without the
  `rumi:header` column, so wide reads carry no `::header` column for those
  files. `header` defaults to `True`, and disabling both `header` and `stats`
  is an error. The setting is stored with the extension configuration.

## 0.10.3 - 2026-09-24

### Added

- `GeoEnrich` defaults to the public 10 km MajorTOM index on Source Cooperative,
  providing its enrichment variables without an Earth Engine account. Earth
  Engine remains available explicitly with `backend="earthengine"`.

## 0.10.2 - 2026-09-23

### Changed

- Filtered `dataset` queries resolve file columns only for matching samples
  instead of pivoting every file in the dataset.
- Python exports rebuild samples in bounded batches and restrict TACOCAT
  origin lookups to the selected samples.
- R and Julia datasets reuse their native handles across reads and SQL
  queries.
- Remote directory datasets validate cached metadata files concurrently.

## 0.10.1 - 2026-09-23

### Changed

- `taco.export()` now requires a SQL query. The query may select rows from any
  dataset relation, while the export always writes complete samples.
- Python datasets reuse their native handles across SQL queries.

### Fixed

- Python exports resolve TACOCAT partitions correctly when the source is the
  release root rather than the `.tacocat` directory.

## 0.10.0 - 2026-09-23

### Changed

- `Dataset.sql()` exposes one `dataset` relation with the same wide sample
  shape returned by `read()`. Raw metadata levels remain available by name.
- `read(files=...)` is the ordered projection of `dataset` for the selected
  structure declarations.

### Removed

- The `data` and `files` SQL relations. Queries can select generated file
  columns from `dataset` or inspect hierarchy levels directly.

## 0.9.1 - 2026-09-23

### Changed

- Maintenance release with no user-facing changes.

## 0.9.0 - 2026-09-23

### Added

- Samples carry a required `id`, passed as `taco.Sample(id=...)` and stored in
  the `id` column of `sample.parquet`. It must be unique across the dataset and
  its TACOCAT partitions; the row position stays the physical identity.
- `MajorTOM` takes `extra`, a mapping of name to distance, so one extension
  produces several codes in its namespace, such as a cell id and a coarser code
  for `partition_by`.

### Changed

- Every contract declares a non-empty `taco:structure`. Single-file datasets
  use one fixed leaf instead of `taco:structure: null`.
- Variable sequences require at least one file, so their lower bound must be
  one or greater.
- Dataset identity no longer includes `dataset_version`. A path ending in
  `.zip` selects a ZIP container and every other path selects a folder.
- Nested list, struct, and map values preserve their nullability in contract
  type names.
- TACOCAT assigns global row identifiers and exposes the sample row position
  as `taco:sample_index` in every public reader view.
- R and Julia expose SQL over the same `data`, `files`, and metadata-level
  relations as Python.
- Python, R, and Julia share the same high-level `read(source, files)` and
  `inspect(source, query)` operations.

### Fixed

- `export()` dropped the sample `id` when subsetting.
- Writers and validators now reject missing, blank, or duplicate sample ids.
- `MajorTOM` preserves fractional distances and records the parameters that
  determine its grid in `COLLECTION.json`.
- Remote FOLDER and TACOCAT cache entries revalidate every metadata object
  before reuse. Appends and removed metadata levels invalidate the cache.
- Source-list index selection is applied after assigning global
  `taco:sample_index` values.
- Passive profile values are checked against canonical field nullability when
  samples are added instead of failing during Parquet serialization.
- The `files` relation orders effective metadata by qualified field name.

## 0.8.2 - 2026-09-18

### Changed

- Package descriptions use one wording across the core, Python, R, Julia, and
  JavaScript packages.
- `@asterisk-labs/taco` follows the TACO version, starting at 0.8.2, and
  replaces the npm 1.0.0 release.

## 0.8.1 - 2026-09-18

### Changed

- Wide reads now use `{file}::location` for calculated file locations and
  `{file}::header` for Rumi headers. The second colon keeps these names separate
  from regular metadata such as `image:location`. Nested paths use `__` in the
  JavaScript reader too, matching the other readers.
- `tasks` is optional in `COLLECTION.json`. When present it must still list at
  least one task. `Collection(tasks=...)` defaults to `None`, and the core,
  Python, R, Julia, and JavaScript readers accept collections without it.
- `MajorTOM` codes start with `MT`, for example `MT10km_0770U_0395R`.
- `GeoEnrich` writes physical units (mm per year, degrees Celsius, soil scale
  factors applied) and returns `null` where the source has no data instead of
  zero. Population comes from GPW v4.11 in people per km2 and is 0 over water
  and Antarctica.

### Fixed

- The JavaScript reader prefixes remote FOLDER file locations with `/vsicurl/`,
  matching the core reader.
- `GeoEnrich` samples on a fixed EPSG:4326 grid, so a value no longer depends
  on which other variables are requested with it.
- `GeoEnrich` descriptions name the real sources and units, the 2020 human
  modification epoch is read directly, and failed Earth Engine requests are
  retried.

## 0.8.0 - 2026-09-15

### Added

- `Dataset.read(files=...)` provides the complete wide sample table, while
  `Dataset.sql(query)` provides partial access through `data`, `files`, and
  named raw level relations.
- `export()` writes the samples of a ZIP, FOLDER, or TACOCAT, local or remote,
  as a new dataset. `samples` is a PyArrow-compatible table, normally selected
  from the `data` SQL relation. Keyword arguments replace fields of the
  collection, such as `id` or `description`; the rest is inherited. The subset
  keeps the contract and recomputes its extent. Without `samples` it
  converts a FOLDER to ZIP or merges a TACOCAT into one dataset. The data is
  copied through Karu, so a small dataset can be cut from a large one on
  Hugging Face, S3, or Source Cooperative while downloading only the payload
  files belonging to its selected samples.
- Remote reads and exports report download progress in interactive terminals.
  Python exports also report copy progress, and writers retain their explicit
  `progress=True` option.
- The metadata cache has readable dataset-based entry names, a configurable
  `TACO_CACHE_SIZE` limit, and least-recently-opened eviction.

### Changed

- Concrete remote datasets trust their cached metadata until
  `TACO_CACHE_REFRESH=1` is set, while mutable version manifests are read again
  so changes to `taco:default_version` remain visible. Local archives are
  invalidated by size and modification time.
- Wide columns replace `/` in structural paths with the reversible `__`
  separator, for example `before/B02.tif` becomes `before__B02.tif`.
- Python `read()` now accepts only `source` and `files`; row filtering and raw
  level access use `Dataset.sql()`.
- Python `read()` orders rows by their sample key and aggregates file locations
  before joining the sample metadata.
- The low-level SQL inspector is named `taco.reader.inspect.native_sql()`;
  the unused legacy `read_table()` helper was removed.

### Fixed

- `export()` rejects sample keys that do not exist and refuses unsafe
  `internal:relative_path` or `internal:source_file` values before using them
  as paths.
- `files` includes a nullable `path` column for datasets whose structure is
  null, and empty native transfers no longer leave progress bars open.
- Writers accept a collection version such as `2.0.0` as a FOLDER directory,
  `export(overwrite=True)` replaces an existing TACO output, and trailing SQL
  line comments no longer break `Dataset.sql()` query wrapping.
- `taco.reader.inspect` is available directly after `import taco` instead of
  depending on another module having imported it first.

## 0.7.0 - 2026-09-15

### Added

- A C++ reader core shared by Python, R, and Julia opens local and remote ZIP,
  FOLDER, and TACOCAT datasets through Karu and generates DuckDB SQL for their
  metadata.
- Versioned dataset roots resolve `taco.json` in one read, use its embedded
  collection, select the declared default version, and expose immutable
  releases directly.
- Python `export()` can write selected samples from a local ZIP, FOLDER, or
  TACOCAT while preserving the contract and recomputing collection summaries.
- The JavaScript reader recognizes suffixless CoZIP archives and preserves
  transport errors while probing ambiguous URLs.

### Changed

- Python, R, and Julia use the native reader instead of loading the published
  `cozip` DuckDB extension. HTTP, S3, GCS, Azure, Hugging Face, and Source
  Cooperative use the same transport path.
- The R source package carries synchronized TACO and Karu source trees rather
  than depending on a source checkout at build time.
- Native archives, Python wheels, Julia artifacts, and R packages are built by
  the cross-platform release workflow.
- `read(layout="long", files=...)` keeps only the selected structural files.

### Fixed

- Remote directory probes preserve authentication, TLS, server, and network
  errors instead of reporting every failure as a missing collection.
- Windows builds use the system certificate store, keep DLL permissions, and
  normalize manifest paths in R and Julia.
- R stops the native transport before unloading, avoiding process-exit hangs
  on Windows toolchains.

### Removed

- The `COZIP_EXTENSION` environment variable; the native core owns container
  access in every binding.

## 0.6.3 - 2026-09-13

### Fixed

- `GeoEnrich` now receives the complete metadata level before applying the
  same globally sorted, concurrent Earth Engine batching used by Taco v2.

## 0.6.2 - 2026-09-13

### Removed

- Writer-time extension graphs and operational configuration are no longer
  serialized as `taco:derived` in `COLLECTION.json`. The active contract keeps
  them only in memory while `run()` computes the declared metadata columns.

## 0.6.1 - 2026-09-13

### Changed

- Rename the standalone `SAC`, `ISAC`, and `TAC` APIs and namespaces to the
  explicit `Spatial`/`spatial`, `ISpatial`/`ispatial`, and
  `Temporal`/`temporal` names. `STAC` and `ISTAC` are unchanged.

## 0.6.0 - 2026-09-13

### Added

- Distinct `SAC`, `ISAC`, and `TAC` metadata profiles for regular spatial,
  irregular spatial, and temporal-only samples. `STAC` and `ISTAC` remain the
  combined spatial-temporal profiles.
- `MajorTOM` and `GeoEnrich` accept a qualified `centroid` dependency, allowing
  them to compose with any spatial profile without copying columns.

## 0.5.1 - 2026-09-13

### Fixed

- `GeoEnrich` once again batches and spatially orders Earth Engine requests,
  groups products by reducer, fills masked numeric values with zero, and
  resolves administrative raster codes to human-readable country, state, and
  district names using lookup tables shipped in the wheel.
- GeoEnrich numeric outputs are rounded explicitly to their declared
  `float32` representation before strict contract validation.

## 0.5.0 - 2026-09-13

### Added

- `Extension` and `ExtensionContext` provide one writer-time metadata API for
  operations with validated sample inputs, declared outputs, dependencies, and
  access to local assets. Extensions are explicitly attached to a metadata
  level and run in dependency order.
- Explicit `taco.extensions.STAC()` and `ISTAC()` operations derive centroids
  and temporal midpoints during `writer.run()`. `MajorTOM` composes with STAC
  through its `stac:centroid` dependency regardless of declaration order.
- `taco.extensions.Rumi()` stores the canonical binary `rumi:header` for local
  `.rumi` assets and can calculate named per-band `rumi:stats`.

### Changed

- Active STAC and ISTAC metadata can run at sample or folder scope according
  to their configured input model.
- Development and release checks exercise real Rumi and antimeridian runtimes
  instead of silently skipping those integrations.

### Removed

- `metadata.asset.Raster` and `RasterStats`. Their manually repeated shape,
  dtype, resolution, and positional statistics duplicated format metadata and
  did not inspect raster assets.

## 0.4.0 - 2026-09-10

### Added

- `@asterisk-labs/taco` 1.0.0, a standalone JavaScript reader for TACO v3
  FOLDER, ZIP, and TACOCAT datasets over HTTP. It supports raw-level reads,
  wide and long layouts, sample and file selection, semantic filters,
  reader-calculated `taco:location` values, and targeted payload range reads.
- A browser TACO viewer backed by the JavaScript reader and the public
  TACO/Rumi fixtures. It maps STAC and ISTAC centroids, navigates the Parquet
  hierarchy for each sample, presents the dataset-wide contract separately,
  and provides copy, download, and Rumi-read actions for payloads.

### Changed

- Python, R, and Julia now expose only the `location` reader argument. Stored
  `cozip:location` and `taco:location` values remain protected and are never
  surfaced as trusted locations.
- The project README now presents all four language bindings and links to the
  viewer, specification, package registries, and release checks.
- Python release checks enforce formatting, linting, strict typing, warnings,
  and branch coverage before building and publishing artifacts.

## 0.3.0 - 2026-09-08

### Changed

- Python, R, and Julia now share `open_dataset()` and `read()`. An open dataset
  exposes its sources, collection, and contract in each language.
- STAC once again represents regular raster chunks with `tensor_shape` and a
  six-value GDAL `geotransform`; it no longer stores a WKB footprint on every
  sample. ISTAC is now a distinct irregular-geometry model instead of an alias
  of STAC. Both retain centroid-based collection summaries, and metadata levels
  must choose one of the two profiles.
- The reader keeps one DuckDB connection per thread.
- Collection and nested sample values are validated before writing.
- Python checks now cover formatting, strict typing, warnings and branch
  coverage.
- The writer internals now follow the build flow explicitly (`core`, `archive`,
  `folder`, `metadata_tables`, and `staging`) and use descriptive class and
  method names. STAC/ISTAC definitions moved to `metadata.spatiotemporal`,
  while `metadata.sample` remains a compatible import facade.

### Removed

- The unused `staging_dir` writer option and `writer.ARCHIVE_SUFFIX` constant.

## 0.2.0 - 2026-09-04

### Changed

- The package is laid out as subpackages: `taco.contract` for the contract
  vocabulary, `taco.writer` for the two writers and `taco.reader` for metadata
  access, with `tacocat` and `validate` beside them. The top level exports ten
  names, down from thirty-eight; everything else is imported from the
  subpackage that owns it.
- `COLLECTION.json` records only the fields the caller declares. Nothing is
  inferred from field names, so the writer no longer guesses an extent from
  `stac:centroid` or `time_start`, and `_extent.py`, `auto_extent`,
  `ExtentSpec`, `compute_extent` and `wkb_bounds` are gone. STAC is not part
  of the core. A TACOCAT still merges the extents its partitions declare,
  which TACO specification 7.5 requires.
- Archives are written as `.zip`. cozip specification 14.5 makes the profile
  byte in the byte-0 index the only authoritative signal, and the extension
  carries no meaning. Outputs are extensionless or end in `.zip`; any other
  suffix is rejected.
- Reading a dataset is the `cozip` DuckDB extension's job. `open_dataset`,
  `TacoDataset` and `vsi_subfile` are gone from the top-level API;
  `taco.reader` forwards to the extension and builds no VSI paths itself. The
  old Python mapping covered four URL schemes and the C++ one covers seven,
  so the two had already drifted.
- The ZIP writer now delegates layout planning and serialization to cozip's
  native TACO ABI through `cozip._taco.plan()` / `write()` instead of driving
  the low-level entry API itself. Archive names, priority ordering, padding
  placement and post-write verification are enforced in C. Local source and
  output paths may contain Unicode.
- `Contract`, `Collection` and the writers raise `taco.errors.*`
  exceptions (`ContractError`, `SampleError`, `CollectionError`,
  `WriterError`, ...). They still subclass `ValueError` / `RuntimeError`.
- Metadata levels without fields may be omitted from `Contract(metadata=...)`
  and from `Sample(metadata=...)`.
- `internal:relative_path` is written in `collection.parquet` too (the sample
  index), matching the normative text of spec section 7.2.
- Lossy value conversions (floats into integer fields, bytes into strings,
  ISO strings into timestamps) are rejected at `add()` time.

### Added

- `taco:structure = null` datasets (one file per sample) in every container.
- Inline `bytes` assets, materialized into the staging directory on `add()`.
- `FolderWriter` / `open_folder()` for FOLDER mode with `append=True` and
  optional hard links.
- Partitioned builds (`partition_size`, `partition_by`) producing
  `<stem>_partNNNN.zip` / `<stem>_<value>.zip` plus a `.tacocat` directory;
  `consolidate()` to build a TACOCAT from existing archives.
- An internal metadata view for ZIP, FOLDER and TACOCAT, used by
  `consolidate` and `validate`.
- `validate()` for structural and contract checks across all three containers.
- Type specification parser supporting `timestamp[unit, tz]`, `list<...>`,
  `struct<...>`, `map<...>`, `decimal128(p, s)`, `fixed_size_list<...>`.
- `parse_size()` and file-name sanitizing for partitioned builds.

### Fixed

- `overwrite=False` uses an atomic no-replace publication step, so a file
  created by another process during the build is left untouched.

### Removed

- Direct use of `cozip.profile()` and the entry-based `cozip_plan` /
  `cozip_finalize` calls.

## 0.1.0 - 2026-09-02

- First prototype: `Contract`, `Asset`, `Sample`, `Collection`, staged
  `TacoWriter` producing profile-2 `.tacozip` archives.
