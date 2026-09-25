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
| `taco.metadata.sample` | `sample` | `Spatial`, `Temporal`, `STAC`, `Split`, `MajorTOM`, `GeoEnrich` |
| `taco.metadata.folder` | `folder` | The three profiles, re-scoped |
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

Three profiles built from STAC Item fields, each with canonical fields, types and
nullability. A level may use at most one. STAC `proj:` fields are stored with a
`proj_` prefix (`stac:proj_code`), because a column has exactly one `:`.

| Profile | Producer supplies | Writer adds | Declare it as |
| --- | --- | --- | --- |
| `temporal` | `datetime`, or `start_datetime` + `end_datetime` | nothing | `temporal=taco.metadata.sample.Temporal` |
| `spatial` | `geometry`, or `proj_code` + `proj_shape` + `proj_transform` | `geometry`, `bbox`, `centroid` | `spatial=taco.extensions.Spatial()` |
| `stac` | both of the above | `geometry`, `bbox`, `centroid` | `stac=taco.extensions.STAC()` |

Canonical declarations, enforced by `Contract._check_profiles` from
`contract/schema.py:PROFILE_FIELDS`:

| Field | Type | Nullable |
| --- | --- | --- |
| `geometry` | `binary` | no |
| `bbox` | `list<double>` | no |
| `centroid` | `binary` (WKB Point, EPSG:4326) | no |
| `datetime`, `start_datetime`, `end_datetime` | `timestamp[us, UTC]` | yes |
| `proj_code` | `string` | yes |
| `proj_shape` | `list<int64>` | yes |
| `proj_transform` | `list<double>` | yes |

Either match that nullability exactly, or make the **whole group** nullable with
`Model | None`. Anything in between raises `SPATIAL metadata at level 'sample' must use
canonical nullability or make the complete optional group nullable`.

The row rules live in the model validators (`check_times`, `check_location` in
`metadata/spatiotemporal.py`) and `taco.validate()` re-applies them to every stored row:

- **Time**: `datetime`, or `start_datetime` and `end_datetime` together; the range is
  inclusive and must not be reversed; `datetime` may sit inside a range. A DEM or an
  annual composite uses a range.
- **Grid**: `proj_code`, `proj_shape`, `proj_transform` all or none. `proj_code` is
  `AUTHORITY:CODE` (`EPSG:32718`), `proj_shape` is `[height, width]`, and
  `proj_transform` is `[a, b, c, d, e, f]` with `x = a*column + b*row + c`,
  `y = d*column + e*row + f`: the STAC and rasterio order, **not** the GDAL
  geotransform. An `EPSG:4326` grid uses longitude as `x`.
- **Footprint**: `geometry` is valid 2D EPSG:4326 WKB (`shapely.is_valid`), not a
  GeometryCollection, and split at 180 degrees when it crosses the antimeridian
  (RFC 7946). An edge longer than 180 degrees is only allowed with both ends on the
  antimeridian or along a pole; otherwise it fails with `geometry crosses the
  antimeridian; split it at 180 degrees as RFC 7946 requires`.
- **bbox**: `[west, south, east, north]`, west and east being the narrowest longitude
  interval covering every part, so `west > east` across the antimeridian. A supplied
  bbox must equal the computed one.

`grid_footprint` reprojects 16 points per grid edge to EPSG:4326 and keeps them all,
so a computed footprint has about 65 vertices (around 1 KB of WKB per row). A grid
across the antimeridian becomes a MultiPolygon; a grid around a pole keeps its
boundary and closes through that pole.
A supplied `geometry` wins over the grid, so a footprint may trace valid data only.
`centroid` is the one point that stands for the sample (MajorTOM and GeoEnrich read
it). With a grid it is `grid_center`: the grid center in its own CRS, reprojected as a
single point, so it never depends on how `geometry` samples the edges. Without a grid
it is `footprint_center(geometry)`, which joins a footprint split at the antimeridian
before taking the centroid. A supplied `centroid` is kept. It is TACO's own field; STAC
has no equivalent in the core Item.

Binding `Spatial` or `STAC` as a plain model (no extension) means nothing computes the
outputs: the producer must supply `geometry`, `bbox` and `centroid` on every row.

Profile mistakes have their own messages:

```
metadata level 'sample' must choose one metadata profile, got SPATIAL, TEMPORAL
STAC metadata at level 'sample' is missing fields ['end_datetime']
field sample.stac:bbox must have type list<double>, got fixed_size_list<double, 4>
datetime is required unless start_datetime and end_datetime are given
geometry is required unless proj_code, proj_shape and proj_transform are given
```

A profile carries a **collection summary**: `Spatial` produces the spatial part of
`extent`, `STAC` also the temporal part, and `Temporal` produces nothing. The summary
streams: every bbox spills to a temporary file as one or two longitude intervals, and
the extent leaves out the largest gap between them, so a dataset crossing the
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
    labels=taco.metadata.collection.Labels(classes=["clear", "cloud", "shadow"]),
    optical=taco.metadata.collection.Optical(sensor="Sentinel-2"),
    poi={"category": "volcano", "catalog": "Wikidata"},
)
```

Every keyword that is not a `Collection` parameter is a **group**, and each field `x`
of group `g` is written to `COLLECTION.json` as `g:x` (`labels:classes`,
`labels:num_classes`, `poi:category`). A group is a mapping or a Pydantic model
instance; `Labels` and `Optical` add computed `num_classes` and `num_bands`. It never
becomes a Parquet column and is never copied into a row. `collection.metadata` holds
the groups as JSON values (`{"poi": {"category": "volcano", ...}}`); read back from
`COLLECTION.json` it also holds the values extensions stored, such as `majortom`.
There is no `CollectionMetadata` and no `metadata=` parameter.

The groups are checked when the collection is built, always as `CollectionError`:

```
pass collection metadata as groups, such as labels=... or poi={...}, instead of metadata=
collection metadata group 'licences' must be a mapping or a Pydantic model, got list; did you mean 'licenses'?
collection metadata group 'poi' needs an instance, such as POI(...)
collection metadata group 'poi' is empty
metadata field 'poi:a:b' must be namespace:field
collection metadata group 'poi' must be JSON serializable
Split is not collection metadata
collection metadata 'majortom:dist_km' conflicts with the active extension
```

The parameters are keyword-only, so a group can never shift them.
`collection.replace(poi={...})` and `taco.export(..., poi={...})` replace a group, and
`poi=None` removes it. A group cannot be named after a parameter (`title`, `tasks`,
...) from Python; `Collection.from_dict` still reads such a namespace from JSON.

`Collection.from_dict` rejects an unknown reserved key (`taco:`, `internal:`,
`cozip:`) and any unknown key without a namespace; unknown qualified keys in a user
namespace are preserved. `extent` and `taco:sources` are writer-generated: the writer
recomputes `extent` on every build and refuses `taco:sources` outside TACOCAT.

Extension collection metadata is merged automatically and must not conflict:
`collection metadata 'majortom:dist_km' conflicts with the active extension`.

`KNOWN_TASKS` in `contract/collection.py` lists the recognized task strings;
an unrecognized one is a validation **warning**, not an error.
