# STAC Extension

- **Title:** STAC
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/stac/v1.0.0/schema.json`
- **Namespaces:** `temporal`, `spatial`, `stac`
- **Scope:** Sample, Folder
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](stac/v1.0.0/schema.json) · [Example](stac/examples/COLLECTION.json)

This extension defines three metadata profiles built from the fields of a STAC Item and its projection extension. `temporal` records when a sample was observed, `spatial` where it is, and `stac` both. The writer derives a `centroid` for the spatial profiles and summarizes them into the collection `extent`.

## Profiles

A metadata level MUST choose at most one profile. A profile is a group of tabular fields, not a serialized STAC Item. Its fields keep their STAC names, except that the `proj:` prefix becomes `proj_` so that every column has a single namespace. For example, STAC `proj:code` is stored as `stac:proj_code`.

| Profile | Producer inputs | Writer outputs |
| --- | --- | --- |
| `temporal` | `datetime`, or `start_datetime` and `end_datetime` | None |
| `spatial` | `proj_code`, `proj_shape`, and `proj_transform`, or `geometry` | `centroid` |
| `stac` | The inputs of both | `centroid` |

A profile MUST use its canonical namespace: `temporal`, `spatial`, or `stac`. The namespace holds exactly the fields of its profile; any other field belongs in another namespace.

## Level fields

Every profile MUST use the following canonical field declarations. A field is present in each profile listed under Applies to. When the complete profile group is optional, every stored column in that group is nullable. Its fields and types remain canonical.

| Field | Applies to | Type | Nullable | STAC field | Meaning |
| --- | --- | --- | --- | --- | --- |
| `geometry` | `spatial`, `stac` | `binary` | Yes | `geometry` | Footprint in EPSG:4326 as WKB, when the producer supplies one |
| `bbox` | `spatial`, `stac` | `list<double>` | Yes | `bbox` | `[west, south, east, north]` of `geometry`, when the producer supplies it |
| `centroid` | `spatial`, `stac` | `struct<lon: float, lat: float>` | No | None | Center of the sample in EPSG:4326 |
| `datetime` | `temporal`, `stac` | `timestamp[us, UTC]` | Yes | `datetime` | Acquisition time |
| `start_datetime` | `temporal`, `stac` | `timestamp[us, UTC]` | Yes | `start_datetime` | First time covered by the observation |
| `end_datetime` | `temporal`, `stac` | `timestamp[us, UTC]` | Yes | `end_datetime` | Last time covered by the observation |
| `proj_code` | `spatial`, `stac` | `string` | Yes | `proj:code` | CRS of the grid |
| `proj_shape` | `spatial`, `stac` | `list<int64>` | Yes | `proj:shape` | Grid height and width in pixels |
| `proj_transform` | `spatial`, `stac` | `list<double>` | Yes | `proj:transform` | Affine transform of the grid |

For example, a valid Temporal profile has exactly these three canonical columns:

```
{
  "temporal:datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Acquisition time; null when only a range is known"},
  "temporal:start_datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "First time covered by the observation, inclusive"},
  "temporal:end_datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Last time covered by the observation, inclusive"}
}
```

Using `timestamp[ms]` or omitting `end_datetime` does not conform to the Temporal profile.

## Collection fields

None in its namespaces. The writer summarizes the profiles into the core `extent` field, as described under Collection extent.

## Row rules

**Time.** Every row MUST have a `datetime`, or both a `start_datetime` and an `end_datetime`. `start_datetime` and `end_datetime` MUST be given together, MUST NOT be reversed, and bound an inclusive range. A row MAY also carry a `datetime` inside its range. An observation without a single acquisition instant, such as a DEM or an annual composite, uses a range.

**Footprint.** A supplied `geometry` MUST be a non-empty, valid, two-dimensional WKB geometry with longitude and latitude coordinates in EPSG:4326. It MUST NOT be a GeometryCollection. A footprint that crosses the antimeridian MUST be split into parts at 180 degrees, as RFC 7946 requires. Therefore no edge may span more than 180 degrees of longitude, except an edge whose two ends lie on the antimeridian or an edge along a pole.

**Bounding box.** A producer MAY supply `bbox` together with `geometry`. It holds `[west, south, east, north]`. `south` and `north` are the latitude bounds of `geometry`. `west` and `east` bound the narrowest longitude interval that covers every part of `geometry`. When that interval crosses the antimeridian, `west` is greater than `east`. When two intervals are equally narrow, the one that does not cross is used.

**Grid.** `proj_code`, `proj_shape`, and `proj_transform` MUST be given together or not at all. `proj_code` has the form `AUTHORITY:CODE`, such as `EPSG:32718`. `proj_shape` holds the positive height and width of the grid in pixels, in that order. `proj_transform` holds six finite coefficients `[a, b, c, d, e, f]` with a non-zero `a*e - b*d`. They map the corner of a pixel to CRS coordinates:

```
x = a * column + b * row + c
y = d * column + e * row + f
```

This is the order of STAC `proj:transform` and of rasterio, not the order of the GDAL geotransform. A grid in `EPSG:4326` uses longitude as `x`.

**Writer outputs.** A row MUST supply a complete grid, `geometry`, or both. The writer derives only `centroid`; `geometry` and `bbox` remain null unless supplied. A supplied `geometry` MAY differ from the grid. A supplied `bbox` MUST match it.

**Grid footprint.** A grid footprint starts from its reprojected corners. Edges are subdivided to half-pixel precision. Antimeridian crossings are split at 180 degrees; polar footprints close through the enclosed pole.

The writer and `taco.validate` MUST reject, on every level, a grid that wraps more than once in its CRS units or extends beyond its CRS domain.

**Centroid.** `centroid` is a float32 EPSG:4326 `{lon, lat}` struct. It is the supplied value, the reprojected grid center, or the centroid of `geometry` after joining antimeridian-split parts. The writer rounds it before other extensions run. It is not exported to STAC.

A row where an optional profile group is absent as a whole is exempt from these rules. `taco.validate` MUST check them on every other row.

## Collection extent

The `spatial` and `stac` profiles produce the collection `extent`. The writer uses the shallowest metadata level that contains one of them. `temporal` has no spatial coverage and does not produce an `extent`.

The spatial interval covers the footprint bounds of every row at that level: its `bbox`, or else the bounds of its `geometry`, or else of its grid footprint. `south` and `north` are their extreme latitudes. `west` and `east` bound the narrowest longitude interval that covers every box, under the rule for `bbox` above.

For `stac`, the temporal interval starts at the earliest `start_datetime` or `datetime` and ends at the latest `end_datetime` or `datetime`.

## Writer

`spatial=taco.extensions.Spatial()` and `stac=taco.extensions.STAC()` declare the producer inputs and compute `centroid`. Temporal computes nothing, so it is declared as a model, as in `temporal=taco.extensions.stac.Temporal`. Inputs use the matching model from `taco.extensions.stac`, or from `taco.extensions.stac.folder` at a folder level.

A level MAY bind the Spatial or STAC model without its extension; the producer then supplies `centroid`.

```python
taco.Level("sample", stac=taco.extensions.STAC())
```
