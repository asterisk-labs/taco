---
name: taco
description: >-
  Use TACO or work on its codebase: package Earth observation data as ZIP, FOLDER or
  TACOCAT datasets; declare a contract, structure and metadata schema; write samples
  with `taco.open_writer`; read and filter them with `open_dataset`, `read` and
  `Dataset.sql`; add writer-time extensions and spatial or temporal profiles; validate,
  export or consolidate a dataset; read from S3, Hugging Face or Source Cooperative; or
  edit TACO's C++ core, Python writer, R, Julia and JavaScript readers, `SPEC.md` and
  tests. Do not use for Rumi frame layout or GeoZL codec work that does not involve a
  TACO dataset.
---

# TACO

TACO packages Earth observation data for machine learning. A dataset is a set of
**samples**, each an atomic unit holding its files and its metadata together. One
**contract** declares the file structure and the metadata schema before the first
sample is written, so the writer can reject a sample that does not fit and a reader
can filter by region, time, cloud cover or split before touching a single payload.

FOLDER and ZIP datasets contain `DATA/`, one Parquet file per metadata level under
`METADATA/`, and `COLLECTION.json`. A TACOCAT contains the collection and merged
metadata, and points to the ZIP partitions that hold the payloads. Readers compute
file locations without extracting payloads: ZIP and TACOCAT use `/vsisubfile/` byte
ranges, while FOLDER uses paths below `DATA/`.

This skill describes **taco-eo 0.10.2** (spec 3.0.0, C API 2, DuckDB 1.5.5, cozip
2026.9.17). Check `taco.__version__`. If it differs, trust the installed source,
`docs/spec/SPEC.md` and `CHANGELOG.md` over this file.

## Mental model

- **The contract is frozen.** `taco.Contract(structure=[...], metadata=...)` declares
  every file a sample may hold and every field each level stores. Changing it makes a
  different dataset that needs a new `id`; an append re-checks it.
- **Levels are rows, not depth.** `sample` has one row per sample, `children` one row
  per direct child of a sample, `children/<folder>` one row per direct child of that
  folder in every sample. One Parquet each, joined by
  `child."internal:parent_id" = parent."internal:current_id"`.
- **The writer is Python; the reader is C++.** `taco.open_writer` is the only
  conforming writer. Python, R and Julia all load `libtaco`, which generates DuckDB
  SQL; JavaScript has a separate pure-JS reader. A reader binding adds no semantics.
- **Reading returns locations, not bytes.** A wide read carries one
  `{file}::location` column per structure leaf. A compatible payload reader then opens
  that path when the bytes are actually needed.

## Canonical workflow

```python
from datetime import datetime, timezone

import taco
from pydantic import BaseModel, Field


class ML(BaseModel):
    split: str = Field(description="Dataset split")


contract = taco.Contract(
    structure=["before/B02.tif", "after/B02.tif", "mask.tif", "extra*[1,3].png"],
    metadata=[
        taco.Level("sample", stac=taco.extensions.STAC(), ml=ML),
    ],
)
collection = taco.Collection(
    contract=contract,
    id="tiny-change",
    description="Paired observations and their change mask",
    licenses=["CC-BY-4.0"],
    providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
    tasks=["change-detection"],
)

payloads = {                                         # bytes or paths, one per declaration
    "before/B02.tif": b"before",
    "after/B02.tif": b"after",
    "mask.tif": b"mask",
    "extra0.png": b"extra",                          # extra*[1,3].png, index 0 of 1
}

with taco.open_writer(collection, "tiny.zip", overwrite=True) as writer:
    writer.add(
        taco.Sample(
            id="lima-0001",                          # stable identity, never the index
            assets=[taco.Asset(data, path=name) for name, data in payloads.items()],
            metadata=taco.Metadata(
                stac=taco.metadata.sample.STAC(      # geometry, bbox and centroid are derived
                    proj_code="EPSG:4326",
                    proj_shape=(256, 256),           # [height, width]
                    proj_transform=(0.2 / 256, 0, -76.6, 0, -0.2 / 256, -12.0),
                    datetime=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                ml=ML(split="train"),
            ),
        )
    )
    writer.run()                                     # the commit point

dataset = taco.open_dataset("tiny.zip")
table = dataset.read()                               # taco:sample_index, id, metadata, locations
masks = dataset.read(files="mask.tif")               # one generated column instead of four
train = dataset.sql('SELECT * FROM dataset WHERE "ml:split" = \'train\'')
assert taco.validate("tiny.zip").ok
```

## Choosing a container

| What you are doing | Output | Why |
| --- | --- | --- |
| Building over time, adding samples later | FOLDER: `open_writer(c, "ds")`, then `append=True` | The only container that appends |
| Publishing one finished dataset | ZIP: `open_writer(c, "ds.zip")` | Immutable, one object, byte-range reads |
| Publishing more than one object holds | `partition_size="4GB"` or `partition_by="ml:split"` | Writes `ds_<label>.zip` beside a `.tacocat/` |
| Combining archives built separately | `taco.consolidate([...])` | Checks contract, collection metadata and `id` uniqueness |
| A subset of an existing dataset | `taco.export(src, out, sql=...)` | Whole samples, new indices, `id` preserved |

Producers **should build FOLDER and publish ZIP**: the logical content is identical,
and only ZIP metadata carries the byte offsets that make random access possible.

## Invariants and pitfalls

- **`id` is identity; `taco:sample_index` is a position.** `id` is the producer's
  string, unique across the dataset and preserved by export and consolidation.
  `taco:sample_index` is generated, never stored, and assigned across a source list in
  source then row order, so reversing the list renumbers it. Never persist it.
- **A source list does not check `id` uniqueness across different sources.** Two
  compatible archives may contain the same `id`, and `open_dataset(["part-a.zip",
  "part-b.zip"])` will return both under distinct indices. Repeating the same source
  path is rejected. `consolidate` rejects duplicate ids across partitions.
- **Two level orders exist.** `Dataset.contract.levels` follows structure declaration
  order (`children/before`, `children/after`); `inspect(path, "levels")` and the native
  handle return the core's sorted order. Both put parents before children. Do not zip
  them together; look levels up by name.
- **Variable sequences are cardinal, not globs.** `img*[2,4].png` matches `img0.png` to
  `img{k-1}.png`, contiguous, no leading zeros, `2 <= k <= 4`. A sample containing
  `img0.png` and `img01.png` reports the matched indices as `got [0]`, because the
  leading-zero name does not match the sequence. Its rows land in one LIST column
  named after the prefix.
- **Generated columns carry `::`.** `/` becomes `__` and the suffix is `::location`, or
  `::header` when the level declares `rumi:header`. User fields contain exactly one
  `:`, so the double colon cannot collide. `before/B02.tif` reads as
  `before__B02.tif::location`.
- **Every user field needs double quotes in SQL.** `WHERE ml:split = 'train'` is a
  DuckDB parser error; write `WHERE "ml:split" = 'train'`.
- **`Dataset.sql` raises DuckDB exceptions, not `TacoError`.** A wrong relation is
  `CatalogException`, bad syntax is `ParserException`. Only opening, contract and
  sample problems raise `TacoError` subclasses.
- **`run()` is the commit point.** `add()` and `extend()` only stage to a temporary
  directory; leaving the context manager cleans up without publishing. `run()` is
  idempotent after success, and `add()` afterwards raises
  `WriterError: add() is only valid while the writer is open; state=succeeded`.
- **Extension output must be row-local.** The writer runs extensions per batch and
  re-runs the first batch on row 0 alone to prove it. A value that aggregates across
  samples belongs in a collection summary, not an extension.
- **Semantic extension parameters travel in `COLLECTION.json`.** `MajorTOM(dist_km=100)`
  stores `majortom:dist_km`, and an append using a different distance is rejected.
  Operational settings such as `batch_size` and `workers` are never stored.
- **A level has at most one spatial or temporal profile, with STAC fields.**
  `temporal` holds `datetime` or an inclusive `start_datetime`/`end_datetime` range;
  `spatial` holds an EPSG:4326 WKB `geometry` and its `bbox`; `stac` holds both. A
  sample gives its footprint or its grid (`proj_code`, `proj_shape` as
  `[height, width]`, `proj_transform` in STAC/rasterio order, **not** GDAL order) and
  the extension computes `geometry`, `bbox` and `centroid` (the exact grid center,
  which MajorTOM uses). Mixing profiles, or using
  `timestamp[ms]`, is a `ContractError`; `temporal` is a plain model, not an extension.
- **Names are deliberately restricted.** The `taco`, `internal` and `cozip`
  namespaces and `__` in names are reserved. Path components reject `<>:"\|?` and a
  trailing space or dot. `*`, `[` and `]` are allowed only in the final component of a
  valid variable-sequence declaration. Structure paths are normalized relative POSIX
  and printable ASCII.
- **Empty data is refused.** Every dataset needs a sample, every sample a declared
  file, and every stored file at least one byte.
- **Remote metadata is cached, payloads are not.** Entries live under `TACO_CACHE_DIR`
  (default `~/.cache/taco`), capped by `TACO_CACHE_SIZE` (10 GiB, least recently opened
  evicted first) and revalidated against the origin size; `TACO_CACHE_REFRESH` forces a
  rebuild. Local FOLDER and TACOCAT are read in place. An open `Dataset` is a snapshot.
- **The core must be loadable.** Python looks for `taco/_lib/libtaco.dylib|so` or
  `TACO_LIB`, and rejects a library whose C API is not 2. `make core` builds it and
  stages the copy Python uses.

## Reference map

Read only the reference the current task needs. Each names its sources in the
repository, and its examples were run against taco-eo 0.10.2.

| Task | Read |
| --- | --- |
| Structure declarations, variable sequences, naming rules, `Contract` equality | [references/contract.md](references/contract.md) |
| Levels, Pydantic groups, types and nullability, collection metadata, profiles | [references/metadata.md](references/metadata.md) |
| `open_writer`, samples and assets, append, partitions, `consolidate`, `export` | [references/writing.md](references/writing.md) |
| `open_dataset`, `read`, `Dataset.sql` relations, source lists, remote sources, cache | [references/reading.md](references/reading.md) |
| Built-in extensions, writing an `Extension`, dependencies, collection summaries | [references/extensions.md](references/extensions.md) |
| `validate()` check by check, every issue code, what a reader rejects | [references/validation.md](references/validation.md) |
| Physical layout, internal columns, ZIP and CoZIP profile, FOLDER, TACOCAT, VSI | [references/format.md](references/format.md) |
| The C++ core: C API, container detection, SQL generation, cache, transport, build | [references/core.md](references/core.md) |
| R, Julia and JavaScript readers: coverage, library loading, the viewer | [references/bindings.md](references/bindings.md) |
| Spec version, migration from v2, release process across five packages | [references/compatibility.md](references/compatibility.md) |
| Error message lookup, library loading, remote failures, repository and test layout | [references/debugging.md](references/debugging.md) |
