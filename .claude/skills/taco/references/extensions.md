# Extensions: writer-time metadata operations

Sources: `python/taco/metadata/_base.py`, `python/taco/extensions/`,
`python/taco/metadata/derived.py`, SPEC 5.4.

An **extension** is a metadata operation the writer runs during `run()`, after assets
are local. It may take producer input, produce columns, or both.

**Extensions are not persisted.** `Contract.to_dict()` emits `taco:structure` and
`taco:metadata` only, as SPEC 8.1 requires: the dependency graph and the configuration
live in the Python contract while writing, and what survives is the produced columns
in `taco:metadata` plus any semantic parameter promoted to collection metadata. A
dataset read back therefore has `contract.extensions == {}` even though the columns
are there.

Readers still tolerate the legacy `taco:derived` object. The writer never emits it,
and new datasets should not author extension descriptors under either `taco:derived`
or `taco:extensions`. The latter is not a collection key: although the lower-level
`Contract.from_dict` contains a compatibility branch for it, `Collection.from_dict`
rejects it before that branch can run.

## Built-in extensions

| Extension | Requires | Produces | Scopes |
| --- | --- | --- | --- |
| `Spatial(model=...)` | `spatial:crs`, `tensor_shape`, `geotransform` | `spatial:centroid` | sample, folder |
| `ISpatial(check_antimeridian=False, model=...)` | `ispatial:crs`, `geometry` | `ispatial:centroid` | sample, folder |
| `Temporal(model=...)` | `temporal:time_start`, `time_end` | `temporal:time_middle` | sample, folder |
| `STAC(model=...)` | the four `stac:` inputs plus `time_end` | `stac:centroid`, `stac:time_middle` | sample, folder |
| `ISTAC(check_antimeridian=False, model=...)` | the `istac:` inputs | `istac:centroid`, `istac:time_middle` | sample, folder |
| `Rumi(stats=False, nodata=None)` | nothing | `rumi:header`, optional `rumi:stats` | sample, asset |
| `MajorTOM(dist_km=100, extra=(), latitude_range=(-85, 85), longitude_range=(-180, 180), sep="_", centroid="stac:centroid")` | the centroid field | `majortom:code` plus one per extra grid | sample |
| `GeoEnrich(variables=None, backend="majortom-index", scale_m=5120, batch_size=250, max_concurrency=8, centroid="stac:centroid", code="majortom:code", index_url=...)` | the 10 km MajorTOM code by default; the centroid field for `earthengine` | one column per variable | sample |

The five profile extensions also carry the producer's input model, so
`taco.Level("sample", stac=taco.extensions.STAC())` declares the inputs and the
outputs at once. Pass `model=` a subclass to add fields:

```python
class CloudSTAC(taco.metadata.sample.STAC):
    cloud_cover: float

taco.Level("sample", stac=taco.extensions.STAC(model=CloudSTAC))
```

The subclass must inherit the matching `taco.metadata.sample.*` model, and the
namespace must be the canonical one: `STAC must use metadata namespace 'stac', got
'st'`.

### Centroids

`raster_centroid` evaluates the affine transform at the grid center and reprojects to
EPSG:4326. `geometry_centroid` loads the WKB with shapely and takes its centroid;
with `check_antimeridian=True` on a geographic CRS it runs `antimeridian.fix_shape`
first, which needs the `taco-eo[antimeridian]` extra. A projected CRS needs `pyproj`:
`projected spatial metadata requires 'pyproj'`. Supplying `centroid` yourself skips
the computation for that row.

### Rumi

`Rumi` needs one local `.rumi` asset per row and calls `rumi.info(source=...)` for the
canonical header; producers must never construct it themselves. With `stats=True` it
also decodes each frame and stores per-band statistics as
`list<struct<minimum: double?, maximum: double?, mean: double?, stddev: double?,
valid_count: int64, nodata_count: int64>>`, excluding non-finite values and the
configured `nodata`. Cube statistics combine the time and spatial axes per band.
Errors: `the Rumi extension requires one local asset for every metadata row`, `the
Rumi extension only accepts .rumi assets, got 'x.tif'`, and
`the Rumi extension requires 'taco-eo[rumi]'`.

A level declaring `rumi:header` makes the reader emit a `{file}::header` column beside
each `{file}::location`.

### MajorTOM and GeoEnrich

`MajorTOM` assigns the spherical grid cell containing each centroid, at `dist_km`
kilometres, plus one column per `extra` grid. An extra grid may not be called `code`,
may not contain `:`, and needs a positive distance. `sep` must contain no
alphanumerics.

`GeoEnrich` attaches elevation, climate, soil, population, and administrative
variables through one of two backends. The default `majortom-index` backend joins
the sample's 10 km MajorTOM identifier against
`https://data.source.coop/major-tom/index/global.parquet`; it needs no Earth Engine
account. Use `MajorTOM(dist_km=10)` before it, or set `code=` to another metadata
field containing those identifiers.

Set `backend="earthengine"` explicitly to sample every centroid. That backend needs
`earthengine-api`: `GeoEnrich requires earthengine-api; install taco-eo[geoenrich]`.
For this backend, both extensions default to `centroid="stac:centroid"`; point them
at `spatial:centroid` or `istac:centroid` when the level uses another profile. The
value must match `[a-z][a-z0-9_]*:centroid`.

`GeoEnrich` sets `__taco_complete_level__`, so its level is buffered whole rather
than flushed per batch. The index backend and source URL are recorded in collection
metadata; Earth Engine keeps the collection metadata written by TACO 0.10.2 so
existing FOLDER datasets remain append-compatible.

## Writing an extension

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import pyarrow as pa
from taco import Extension, ExtensionContext


@dataclass(frozen=True)
class Area(Extension):
    """Area of each sample footprint, in square degrees."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    @property
    def requires(self) -> tuple[str, ...]:
        return ("stac:tensor_shape", "stac:geotransform")

    @property
    def fields(self) -> pa.Schema:                     # unqualified; the namespace is added
        return pa.schema([pa.field("deg2", pa.float64(), nullable=False,
                                   metadata={b"description": b"Footprint area in square degrees"})])

    def configuration(self) -> Mapping[str, Any]:      # active writer contract only
        return {}

    def collection_metadata(self) -> Mapping[str, Any]:  # stored in COLLECTION.json
        return {}

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        shapes = context.columns["stac:tensor_shape"]
        transforms = context.columns["stac:geotransform"]
        return {"deg2": [abs(s[-1] * t[1] * s[-2] * t[5]) for s, t in zip(shapes, transforms)]}


taco.Level("sample", stac=taco.extensions.STAC(), area=Area())
```

Contract:

- `requires` names **fully qualified** fields, which may come from another group or
  another extension's output. `fields` names **unqualified** fields, which the writer
  prefixes with the group's namespace. At least one output field is mandatory.
- `input_model` is optional; return a Pydantic model to also accept producer values.
  A name appearing in both must use the same Arrow type: `extension output
  'rumi:header' has type binary, but its input model declares string`.
- `run` receives one batch: `context.level`, `context.columns` (every column present
  in those rows, by qualified name) and `context.assets` (the local `Path` of each
  row's asset, or `None`). It must return exactly its declared field names, each a
  sequence of the same length as the batch.
- `DerivedMetadata` is the older column-only base: implement `compute(columns)` and it
  is adapted to `run`. New extensions should implement `Extension`.
- `__taco_complete_level__ = True` buffers the whole level instead of flushing per
  batch, for an operation that needs every row at once.

### Row independence

Output must depend only on its own row. On the first useful batch of each level the
writer re-runs the extension on row 0 alone and compares; a mismatch raises:

```
extension group 'area' depends on the other rows of its batch; it runs once per
batch, so a value that aggregates across samples belongs in a collection summary
```

This exists because `batch_size` would otherwise change the stored data. A dataset-wide
value belongs in a `CollectionSummary`.

### Dependency resolution

The writer sorts extensions by their `requires`, so declaration order does not matter.
Three failures are contract errors:

```
extensions at 'sample' require missing fields ['spatial:centroid']
extensions at 'sample' contain a dependency cycle
extensions at 'sample' produce a field more than once
```

Runtime failures are sample errors: a non-mapping return, wrong field names
(`extension group 'area' returned ['area'], expected ['deg2']`), a wrong row count, or
a value that will not coerce (`invalid extension output 'area:deg2' at 'sample': ...`).

### Configuration versus collection metadata

`configuration()` records how the group was set up in the live contract descriptor;
it is descriptive and, per the note above, is not written to disk. `collection_metadata()` returns parameters that change what a stored
column **means**, and the writer promotes them to qualified keys in `COLLECTION.json`
under the group's namespace. `MajorTOM(dist_km=100)` stores `majortom:dist_km: 100`,
so an append with `dist_km=50` is refused rather than mixing two grid sizes in one
column. Operational settings (batch size, workers, credentials, temporary paths) must
never be stored.

Two extensions declaring the same collection key with different values raise
`extensions declare conflicting collection metadata 'majortom:dist_km'`, and a
collection value that contradicts an active extension raises `collection metadata
'majortom:dist_km' conflicts with the active extension`.

## Collection summaries

A `CollectionSummary` is a streaming reducer attached to a metadata model through
`__taco_summaries__`. It declares `field` (the `COLLECTION.json` key it writes) and
`requires` (unqualified field names read from its own namespace), and implements
`update(columns)`, `finish()` and `close()`. The writer feeds it every batch, including
rows copied during an append, and writes `finish()` into the collection; returning
`None` removes the key.

`_SpatialExtent` and `_SpatioTemporalExtent` in `metadata/spatiotemporal.py` are the
built-ins behind `extent`. They keep only what a summary needs: latitudes reduce to a
running min and max, longitudes spill to a temporary file and are sorted with a memmap
so the widest gap, and therefore the antimeridian-aware west and east, can be found
without holding the table in memory.
