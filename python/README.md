# taco

Installed as `taco-eo`, imported as `taco`, because the name `taco` is taken
on PyPI. It is the reference Python writer for [TACO v3](https://asterisk.coop/taco/spec)
datasets: contract-first Earth Observation collections stored as
cloud-optimized ZIP archives, plain folders, or consolidated TACOCAT catalogs.

- **Contract-first.** Declare the sample structure (`taco:structure`) and the
  metadata schema (`taco:metadata`) once; every sample is validated on `add()`.
- **`.zip` archives** are produced through [cozip](https://github.com/asterisk-labs/cozip)
  profile `TACO` (value `2`): STORE-only entries, a byte-0 index that points at
  `COLLECTION.json` and every `METADATA/*.parquet`, exact `internal:offset` /
  `internal:size` byte ranges for `/vsisubfile/` access.
- **FOLDER mode** for datasets under construction, including incremental
  append.
- **TACOCAT** consolidation of many partitions, produced automatically when a
  build is partitioned by size or by a collection-level field.
- **Validation** of any container against the specification.

The package depends on `cozip`, DuckDB and PyArrow.

## Install

```bash
pip install taco-eo
```

`taco` needs a `cozip` release that ships the native TACO writer ABI and its
Python adapter, `cozip._taco`.

## Quick start

```python
from datetime import datetime, timezone

from taco import Collection, Contract, Sample, open_writer

contract = Contract(
    structure=["before/B02.tif", "before/B03.tif", "after/B02.tif", "after/B03.tif", "change_map.tif"],
    metadata={
        "collection": {
            "stac:centroid": ["binary", "Center point in EPSG:4326 (WKB)"],
            "stac:time_start": ["timestamp[us]", "Acquisition start"],
            "split": ["string", "Dataset split"],
            "change_ratio": ["double", "Percentage of changed pixels"],
        },
        "sample": {"sensor": ["string", "Sensor name"]},
        "sample/before": {"resolution": ["double", "Spatial resolution in metres"]},
        "sample/after": {"resolution": ["double", "Spatial resolution in metres"]},
    },
)

collection = Collection(
    contract=contract,
    id="change-detection",
    dataset_version="1.0.0",
    description="Land cover change detection dataset",
    licenses=["CC-BY-4.0"],
    providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
    tasks=["change-detection"],
)

sample = Sample(
    assets={
        "before/B02.tif": "/data/t1/B02.tif",
        "before/B03.tif": "/data/t1/B03.tif",
        "after/B02.tif": "/data/t2/B02.tif",
        "after/B03.tif": "/data/t2/B03.tif",
        "change_map.tif": "/data/change_map.tif",
    },
    metadata={
        "collection": {
            "stac:centroid": centroid_wkb,
            "stac:time_start": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "split": "train",
            "change_ratio": 0.15,
        },
        "sample": {
            "before": {"sensor": "S2A"},
            "after": {"sensor": "S2B"},
            "change_map.tif": {"sensor": "S2B"},
        },
        "sample/before": {"B02.tif": {"resolution": 10.0}, "B03.tif": {"resolution": 10.0}},
        "sample/after": {"B02.tif": {"resolution": 10.0}, "B03.tif": {"resolution": 10.0}},
    },
)

with open_writer(collection, "change_detection.zip") as writer:
    writer.add(sample)        # validation + staging journal only
    result = writer.run()     # plans offsets, writes Parquets once, publishes atomically

print(result.path, result.samples, result.data_files)
```

`COLLECTION.json` carries exactly the fields you declare. TACO has no opinion
about what your metadata means, so nothing is inferred from field names; pass
`Collection(extent=...)` if you want a spatial or temporal extent recorded.

## Layout

The package is small on purpose. Ten names are public; everything else is
reachable through a subpackage.

```
taco              Contract, Collection, Sample, Asset, open_writer, open_folder,
                  consolidate, validate, TacoError, __version__
taco.contract     the contract vocabulary: Provider, Curator, Extent, Leaf, Node,
                  type strings, level and path naming
taco.writer       TacoWriter, FolderWriter, BuildResult, WriterState
taco.reader       a thin wrapper over the cozip DuckDB extension
taco.errors       the full exception hierarchy under TacoError
```

## Concepts

| Object | Role |
| --- | --- |
| `Contract(structure, metadata)` | Immutable `taco:structure` (fixed leaves, variable leaves `prefix*[a,b].ext`, or `None`) plus `{level: {field: [type, description]}}`. Levels are `collection`, `sample`, `sample/<folder>`; levels without fields may be omitted. |
| `Asset(path, source)` | One file of a sample. `source` is a local path or raw bytes. |
| `Sample(assets, metadata)` | Assets keyed by contract path (or a single source for `structure=None`) plus metadata per level. |
| `Collection(contract, id, dataset_version, ...)` | Everything that goes into `COLLECTION.json`, including optional `title`, `curators`, `keywords`, `extent` and free `extra` keys. |
| `TacoWriter` / `open_writer()` | Staged, explicit writer for immutable `.zip` archives. |
| `FolderWriter` / `open_folder()` | Same lifecycle for FOLDER mode, with `append=True` to grow an existing dataset. |
| `BuildResult` | What `run()` produced (path, sample and file counts, size, partitions). |

Types are Parquet/Arrow type strings: `int32`, `double`, `string`, `binary`,
`bool`, `timestamp[us]`, `timestamp[ms, UTC]`, `date32`, `list<double>`,
`struct<a: int32, b: string>`, `decimal128(10, 2)`, and so on. Values are
checked on `add()`; lossy conversions (`3.5` into `int32`) are rejected.

## Writer lifecycle

```
OPEN --add()/extend()--> OPEN --run()--> RUNNING --> SUCCEEDED | FAILED
                             --close()--> CLOSED
```

- `add()` returns the sample's zero-based index. Inline `bytes` assets are
  written to the staging directory immediately; file assets are only
  stat-checked (must exist, be regular and non-empty).
- `run()` is idempotent for a successful writer and never runs implicitly on
  `close()` or when leaving the `with` block.
- Output is written to a temporary file next to the destination and renamed
  into place. `overwrite=True` replaces an existing archive.
- `staging_dir=` controls where the journal, inline assets and Parquet files
  are staged; `row_group_size=`, `batch_size=` and `parquet_options=`, which
  takes any `pyarrow.parquet.ParquetWriter` argument, tune the metadata files.

## Partitions and TACOCAT

```python
with open_writer(collection, "dataset.zip", partition_size="4GB") as writer:
    writer.extend(samples)
    result = writer.run()

result.parts    # (dataset_part0001.zip, dataset_part0002.zip, ...)
result.path     # <dir>/.tacocat with merged Parquets + COLLECTION.json
```

`partition_by="split"` creates one archive per value of a collection-level
field instead. Existing archives can be consolidated by hand with
`taco.consolidate([...], output_dir)`.

Each partition is published atomically, but the complete set is not a single
transaction. If a partitioned build is interrupted, archives completed before
the failure remain on disk and can be replaced by retrying with
`overwrite=True`.

## FOLDER mode

```python
from taco import open_folder

with open_folder(collection, "dataset/") as writer:
    writer.extend(first_batch)
    writer.run()

with open_folder(collection.replace(dataset_version="1.1.0"), "dataset/", append=True) as writer:
    writer.extend(more_samples)
    writer.run()
```

`link=True` hard-links assets into `DATA/` instead of copying when the
filesystem allows it.

## Reading a dataset

`taco.reader` is a thin forwarder to the cozip DuckDB extension, whose
`read_taco()` is one C++ implementation shared by Python, R and Julia. Nothing
is reimplemented in Python: no index parser, no Parquet reader, no VSI paths.

```python
from taco.reader import read

read("dataset.zip")                              # one row per sample
read("dataset.zip", pivoted=False)               # one row per file
read("dataset.zip", idx=5)                       # one sample
read("dataset.zip", idx=[0, 100])                # a half-open range
read("dataset.zip", level="sample/before")       # one contract level, raw
read("dataset.zip", files=["image.tif"])         # only some structure leaves
read("dataset.zip", gdal_vsi=False)              # leave the path columns NULL
```

Every parameter of `read_taco()` is exposed, and the result is a
`pyarrow.Table`. The default shape is one row per sample with one column per
file of the contract, each holding a GDAL path that opens the file without
extracting the archive.

```python
import rasterio
from taco.reader import read

table = read("dataset.zip", idx=[0, 64])
with rasterio.open(table.column("image.tif")[0].as_py()) as src:
    image = src.read()
```

`contract()`, `structure()`, `levels()`, `collection()` and `profile()`
forward to the extension's other functions, and `sql()` returns the query
`read_taco` would run.

The extension is not on the DuckDB community registry yet. Until it is, set
`COZIP_EXTENSION` to a local build of
[cozip_reader](https://github.com/asterisk-labs/cozip_reader); after that the
reader installs it on first use.

## Validation

`taco.validate(path)` returns a `ValidationReport` with errors and warnings
covering the cozip index and profile, STORE-only entries, the priority block,
METADATA schemas and internal columns, parent links, relative paths versus the
contract, and offsets versus the actual ZIP entries (`check_data=False` skips
the file-level checks).

## Specification notes

The package follows TACO v3.0.0 and cozip 1.1.0. Internal columns use
`uint64`; `internal:relative_path` is written at every level (including
`collection`, where it is the sample index) and `internal:offset` /
`internal:size` only in ZIP mode. When `taco:structure` is `null` each sample
is the file `DATA/<index>` and `collection.parquet` carries its byte range.
