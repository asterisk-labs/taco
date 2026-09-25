# Metadata: levels, groups, types and profiles

Sources: `python/taco/contract/schema.py`, `contract/types.py`,
`python/taco/metadata/`, SPEC 5.4 to 5.6.

Tabular metadata describes samples and the nodes inside them, one Parquet per level.
Collection metadata describes the dataset once and lives in `COLLECTION.json`. The two
are separate systems and their objects are not interchangeable.

## Declaring levels

`Contract(metadata=...)` takes a list of `Level` objects; each keyword becomes a
**namespace**. Level names must be unique in the list.

```python
metadata=[
    taco.Level("sample", stac=taco.extensions.STAC(), ml=ML, majortom=taco.extensions.MajorTOM()),
    taco.Level("children", node=Kind | None),        # Model | None makes the group optional
    taco.Level("children/before", file=AssetInfo),
]
```

A level not named in the list stores no user fields, which is valid; the Parquet
file still exists with its internal columns. Naming a level the structure does not
imply fails: `metadata has unknown levels ['children/nope']; valid levels are
['sample', 'children']`.

A namespace matches `[a-z][a-z0-9_]*`; `taco`, `internal` and `cozip` are reserved.
A field name must be non-empty and contain no `:`, `/`, `__` or NUL. The qualified
name `namespace:field` must be unique in its level. The same namespace may appear at
several levels with different schemas.

A raw mapping works too, and is what `COLLECTION.json` round-trips:

```python
taco.Contract(
    structure=["a.tif"],
    metadata={"sample": {"quality:score": {"type": "int64", "nullable": False, "description": "Score"}}},
)
```

Shorthands accepted by `_raw_field`: a bare type string or `pa.DataType` means
`nullable=False` with no description; a two-element sequence means
`(type, description)` with `nullable=True`.

## Scopes

A group is bound to what it can describe through `__taco_scopes__`:

| Module | Scope | Contents |
| --- | --- | --- |
| `taco.metadata.sample` | `sample` | `Spatial`, `ISpatial`, `Temporal`, `STAC`, `ISTAC`, `Split`, `MajorTOM`, `GeoEnrich` |
| `taco.metadata.folder` | `folder` | The five profiles, re-scoped |
| `taco.metadata.asset` | `asset` | `Scaling` |
| `taco.metadata.collection` | `collection` | `Labels`, `Optical`, `Publications`, `SplitStrategy` |

The check runs at contract time against what the level can hold: `sample` holds a
sample, a `children/...` level holds folders and files depending on the structure
beneath it. A required group must cover every possibility; an optional one need only
intersect. `Split cannot be used at metadata level 'children'` is this check.

A model with no `__taco_scopes__`, such as a plain `BaseModel` you define, fits
anywhere. `flatten_metadata` re-checks per row, which matters when one level holds
both kinds: with `structure=["x/y.tif", "z.tif"]`, `children` holds the folder `x` and
the file `z.tif`, so an optional asset-scoped group passes the contract check and is
refused only when it is attached to the folder:
`SampleError: Scaling cannot describe a folder`.

## Python types to Arrow

`_arrow_type` in `schema.py` maps annotations:

| Annotation | Arrow |
| --- | --- |
| `str`, `bytes`, `bool`, `int`, `float` | `string`, `binary`, `bool`, `int64`, `double` |
| `datetime`, `date`, `Decimal` | `timestamp[us]`, `date32`, `decimal128(38, 9)` |
| `list[T]`, `tuple[T, ...]` | `list<T>` |
| `tuple[T, T, T]` | `fixed_size_list<T, 3>` |
| `dict[K, V]` | `map<K, V>` |
| A nested `BaseModel` | `struct<...>` |
| `Literal["a", "b"]` | the shared type of its values |

Anything else raises `unsupported metadata annotation ...; use Annotated[T,
pyarrow_type]`. Use `Annotated` for an exact width:

```python
Float32 = Annotated[float, pa.float32()]
TimestampUTC = Annotated[datetime, pa.timestamp("us", tz="UTC")]
Int32List = Annotated[list[int], pa.list_(pa.int32())]
```

Nullability has two levels. `Model | None` at the `Level` makes the whole group
optional and every stored column nullable; `value: T | None` inside a model makes one
field nullable. In serialized types a `?` suffix marks a nullable nested value:
`list<int64?>` allows null items, `struct<minimum: double?, valid_count: uint64>`
allows a null minimum. The top-level type must not use `?`; top-level nullability is
the field's own `nullable`. Map keys are never nullable.

`Field(description=...)` becomes the Arrow field metadata key `description`, which
validation compares against the contract.

`parse_type` accepts the canonical names plus aliases (`float32`, `float64`,
`float16`, `boolean`, `utf8`, `str`, `bytes`, `int`, `integer`, `long`, `datetime`)
and `type_name` writes the canonical form back.

`coerce_value` validates before pyarrow can silently convert: a `bool` is not an
integer, an `int` is not a `bool`, a naive `datetime` is refused by a `tz`-bearing
timestamp and vice versa, a struct with unexpected fields is refused. Failures surface
as `invalid quality:score at 'sample': ...`.

## Spatial and temporal profiles

Five profiles, each with canonical fields, types and nullability. A level may use at
most one.

| Profile | Producer supplies | Writer adds | Geometry |
| --- | --- | --- | --- |
| `spatial` | `crs`, `tensor_shape`, `geotransform` | `centroid` | regular affine grid |
| `ispatial` | `crs`, `geometry` | `centroid` | irregular WKB footprint |
| `temporal` | `time_start`, optional `time_end` | `time_middle` | none |
| `stac` | spatial inputs plus `time_start` | `centroid`, `time_middle` | regular affine grid |
| `istac` | ispatial inputs plus `time_start` | `centroid`, `time_middle` | irregular WKB |

Canonical declarations, enforced by `Contract._check_profiles`:

| Field | Type | Nullable |
| --- | --- | --- |
| `crs` | `string` | no |
| `tensor_shape` | `list<int64>` | no |
| `geotransform` | `list<double>` | no |
| `geometry` | `binary` | no |
| `time_start` | `timestamp[us, UTC]` | no |
| `time_end`, `time_middle` | `timestamp[us, UTC]` | yes |
| `centroid` | `binary` | no |

Either match that nullability exactly, or make the **whole group** nullable with
`Model | None`. Anything in between raises `STAC metadata at level 'sample' must use
canonical nullability or make the complete optional group nullable`.

Profile mistakes have their own messages:

```
metadata level 'sample' must choose either STAC or ISTAC, not both
metadata level 'sample' must choose one metadata profile, got SPATIAL, TEMPORAL
metadata level 'sample' puts geometry in STAC; use the ISTAC group for irregular footprints
metadata level 'sample' puts affine-grid fields in ISTAC; use the STAC group for regular chunks
STAC metadata at level 'sample' is missing fields ['centroid', 'time_middle']
field sample.stac:time_start must have type timestamp[us, UTC], got timestamp[ms]
```

`geotransform` is the six GDAL affine coefficients `(x_origin, pixel_width,
row_rotation, y_origin, column_rotation, pixel_height)`, all finite.
`raster_centroid` reads them in that order. `tensor_shape` needs at least two positive
dimensions and ends in height and width. `centroid` is always an EPSG:4326 WKB point;
supply it to override the computed one. `point_from_wkb` validates byte order, a
point geometry type, the optional SRID flag and the EPSG:4326 bounds.

A profile carries a **collection summary**: `Spatial` and `ISpatial` produce the
spatial part of `extent`, `STAC` and `ISTAC` also the temporal part, and `Temporal`
produces nothing. The summary streams: centroids spill to a temporary file and the
longitude interval is chosen from the largest gap, so a dataset crossing the
antimeridian gets `west > east` rather than a band around the whole globe. If a
profile appears at several levels, the shallowest owns `extent`.

## Collection metadata

```python
collection = taco.Collection(
    contract=contract,
    id="cloud-segmentation",          # no '/', '\', ':' or NUL; non-empty
    description="Cloud segmentation dataset",
    licenses=["CC-BY-4.0"],           # at least one
    providers=[{"name": "CSIC", "roles": ["producer"]}],   # at least one, name required
    tasks=["segmentation"],           # optional; unknown values warn in validate()
    title="CloudSEN12",
    curators=[{"name": "Cesar Aybar", "email": "cesar@asterisk.coop"}],
    keywords=["clouds"],
    metadata=taco.CollectionMetadata(
        labels=taco.metadata.collection.Labels(classes=["clear", "cloud", "shadow"]),
        optical=taco.metadata.collection.Optical(sensor="Sentinel-2"),
    ),
)
```

`CollectionMetadata.flatten()` writes `labels:classes`, `labels:num_classes`,
`optical:sensor` and so on as flat qualified keys in `COLLECTION.json`. It never
becomes a Parquet column and is never copied into a row. `Labels` and `Optical` add
computed `num_classes` and `num_bands`. Values must be JSON serializable with no NaN
or Infinity.

`Collection.from_dict` rejects an unknown reserved key (`taco:`, `internal:`,
`cozip:`) and any unknown key without a namespace; unknown qualified keys in a user
namespace are preserved. `extent` and `taco:sources` are writer-generated: the writer
recomputes `extent` on every build and refuses `taco:sources` outside TACOCAT.

Extension collection metadata is merged automatically and must not conflict:
`collection metadata 'majortom:dist_km' conflicts with the active extension`.

`KNOWN_TASKS` in `contract/collection.py` lists the recognized task strings;
an unrecognized one is a validation **warning**, not an error.
