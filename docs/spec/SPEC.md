# The TACO Specification

**Version:** 3.0.0

**Status:** Draft

## 1. Version

This document defines version 3.0.0 of the TACO specification. The specification follows Semantic Versioning. Implementations are versioned separately and declare which version of the specification they support.

Every dataset MUST store this version in `COLLECTION.json` as `taco:version`. A reader MUST reject a version it does not support.

TACO v3 is not compatible with v2 datasets. Annex A explains the differences and how to migrate.

### 1.1. Terminology

The words "MUST", "MUST NOT", "SHOULD", "SHOULD NOT", and "MAY" have the meanings defined by RFC 2119 and RFC 8174 when written in uppercase. All other text is explanatory.

## 2. Overview

TACO stands for Transparent Access to Cloud-Optimized datasets. It defines how Earth observation data and metadata are organized, stored, and accessed as one unit.

Several formats can represent Earth observation datasets, but they make different tradeoffs. STAC favors discovery and interoperability through JSON Items, but a large training dataset may require millions of separate JSON files. STAC-GeoParquet solves this problem by storing Items as rows in Parquet, but keeps each one flat and cannot represent hierarchical samples.

Training datasets for artificial intelligence for Earth observation, or AI4EO, often need a shared sample structure, nested files, metadata at different levels, and direct access to each file. TACO is designed around those requirements. It stores the dataset as a collection of samples that follow one declared structure and metadata schema.

|  | STAC | STAC-GeoParquet | TACO |
| --- | --- | --- | --- |
| Data model | Collection, Catalog, Item, Asset | Collection, Item (row), Asset (struct) | Collection, Contract, Sample, Folder, Asset |
| Metadata storage | 1 JSON per Item | 1 Parquet per Collection | 1 Parquet per tree level |
| Serverless query method | Catalog traversal | Columnar query | Columnar query |
| Hierarchical samples | Yes | No (flat rows) | Yes (structural contract) |
| Shared sample contract | No | No | Yes |
| Self-contained portable unit | No | No | Yes |

## 3. Foundations

TACO combines three existing technologies. Apache Parquet stores queryable metadata. VSI paths identify data on local or remote storage. Cloud-Optimized ZIP packages data and metadata while preserving direct access to individual files.

A dataset may use a FOLDER or ZIP container. FOLDER stores the dataset as a directory. ZIP stores a dataset or partition in one archive. TACOCAT is a catalog that combines the metadata of several ZIP partitions.

### 3.1. Apache Parquet

Apache Parquet stores values by column rather than by row. This lets a query avoid data it does not need.

**Predicate pushdown** uses row-group statistics to skip groups that cannot match a filter. A query such as `WHERE "quality:cloud_cover" < 10` can therefore avoid reading most of the file.

**Projection pushdown** reads only the requested columns. Selecting two columns from a table of fifty does not require reading the other forty-eight.

TACO MUST store tabular metadata in Parquet, with one file for each level of the sample hierarchy. Each file can be queried independently.

A reader SHOULD use predicate and projection pushdown when its query engine supports them. Opening a dataset does not require loading every Parquet file or materializing all metadata.

### 3.2. VSI Path Convention

TACO uses VSI paths to locate data. A VSI path describes how to reach a file on local disk, over HTTP, in object storage, or within a byte range of another file.

```
/vsicurl/https://example.com/scene.tif     reads from HTTP
/vsis3/bucket/scene.tif                     reads from S3
/vsigs/bucket/scene.tif                     reads from Google Cloud
/vsiaz/container/scene.tif                  reads from Azure
```

TACO mainly uses `/vsisubfile/`, which identifies a byte range by its offset and size.

```
/vsisubfile/1024_4096,/data/archive.zip
```

This path reads 4096 bytes at offset 1024 from `archive.zip`. It does not extract the archive. VSI paths can also be combined to read a byte range from a remote file.

```
/vsisubfile/1024_4096,/vsicurl/https://hf.co/dataset.zip
```

A reader MUST construct VSI paths when data is accessed. For ZIP containers, it reads the offset and size from Parquet metadata. For FOLDER containers, it builds the path directly from the contract and the stored sample directory, such as `DATA/42/before/B02.tif`.

TACO defines the path convention, not the library that resolves it. GDAL supports VSI paths through tools and libraries such as rasterio, QGIS, gdalwarp, sf, terra, and ArchGDAL. Other implementations are valid if they interpret the same paths and perform the required reads.

### 3.3. Cloud-Optimized ZIP

A cloud-optimized ZIP is a valid ZIP archive that any standard ZIP tool can open. Its first entry begins at byte 0 and contains an index with direct offsets to the metadata files. A TACO reader can use this index without first reading the ZIP Central Directory at the end of the archive.

The index points to the Parquet metadata. The metadata then provides the offsets and sizes needed to access individual data files.

A TACO ZIP MUST conform to CoZIP 1.1.0 profile 2. Its index MUST include `COLLECTION.json` and every Parquet file in `METADATA/`.

Every entry MUST use STORE mode. Compression is invalid because `/vsisubfile/` reads the stored byte range directly.

STORE mode preserves the original file bytes inside a rebuilt archive. A platform that uses [Content-Defined Chunking](https://huggingface.co/docs/xet/chunking) may therefore reuse unchanged chunks instead of uploading them again. Producers that rebuild ZIP datasets frequently SHOULD use CDC-aware storage.

## 4. Design Goals

TACO follows five design requirements.

**Self-contained.** A FOLDER or ZIP contains its data, metadata, and contract. Reading it does not require a database, API server, or external catalog.

**Remote access.** A reader can query metadata and fetch one file without downloading or extracting the complete dataset.

**One contract.** The contract declares every file and metadata field expected in a sample. The writer rejects missing files, undeclared files, and incompatible metadata.

**Dataset description.** Collection fields cover identity, licensing, providers, tasks, and spatial or temporal summaries. These fields do not by themselves guarantee data quality or accessibility.

**Independent readers.** Readers may use different languages and query engines, but they must interpret the stored contract and return the same logical content.

### 4.1. Tradeoffs

Every sample follows one contract, which is fixed when the dataset is created. New samples may be added only when they preserve its structure and metadata schema.

Changing the structure, fields, types, or field semantics creates a different dataset and requires a new `id`. TACO is not suitable when samples must evolve independently.

## 5. Data Model

The contract has two parts. The structure defines the files in each sample, and the tabular metadata schema defines the fields stored at each level. The collection adds the description and global metadata shared by the complete dataset.

### 5.1. Contract

The contract combines the structure and metadata schema of a dataset. It MUST be declared before any sample is written, and every sample MUST follow it. The contract cannot change after the dataset is created.

### 5.2. Structure

The structure defines which files belong to a sample and how they are arranged in folders. It is stored in `taco:structure` as a non-empty list of relative file paths. Every file in a sample MUST be declared in this list. Folders are inferred from the path segments.

Every path MUST be a normalized relative POSIX path. It MUST use `/` as its separator and MUST NOT contain an empty component, `.`, `..`, a leading slash, a trailing slash, or a backslash. Every component MUST use printable ASCII so that the same contract is valid in FOLDER and ZIP containers.

Each path describes either a fixed file or a variable sequence of files.

Every data file in a sample MUST match exactly one declaration in `taco:structure`. Files that are not declared by the structure are invalid. Writers MUST NOT add placeholder, marker, or auxiliary files under `DATA/`.

A **fixed file** appears exactly once in every sample. Its name is written directly in the structure, such as `B02.tif`.

A **variable sequence** allows the number of files to differ between samples. It uses the form `prefix*[a,b].ext`, where `a` is the minimum number of files and `b` is the maximum. The prefix is required and `1 <= a <= b`.

The `*` represents a zero-based index, not a filesystem glob. If a sample contains `k` files, their names MUST run from `prefix0.ext` to `prefix{k-1}.ext` without gaps, where `a <= k <= b`. An index MUST NOT contain leading zeros.

Entries in the same folder MUST have unique identifiers. The identifier is the folder name, the complete fixed filename, or the prefix of a variable sequence.

A folder or fixed file MUST NOT have a name that a variable sequence can produce. Two variable sequences MUST NOT be able to produce the same filename.

These comparisons ignore letter case, so `B02.tif` and `b02.tif` conflict: case-insensitive file systems and SQL identifiers treat them as one name.

The order of `taco:structure` is significant. A folder takes the position of its first path. Fixed files and folders follow declaration order, while instances of a variable sequence follow their numeric index. Writers MUST use this order when assigning child row identifiers.

#### Examples

**Single file.** A dataset with one file per sample declares that file explicitly.

```
{ "taco:structure": ["data.tif"] }
```

**Fixed files.** Each CloudSEN12 sample contains the same three files.

```
{
  "taco:structure": [
    "s2_l1c.tif",
    "s2_l2a.tif",
    "target.tif"
  ]
}
```

**Folders.** Each change detection sample contains two folders and one label.

```
{
  "taco:structure": [
    "before/B02.tif",
    "before/B03.tif",
    "before/B04.tif",
    "after/B02.tif",
    "after/B03.tif",
    "after/B04.tif",
    "change_map.tif"
  ]
}
```

**Variable sequence.** Each multitemporal sample contains between 4 and 16 images.

```
{ "taco:structure": ["img*[4,16].tif"] }
```

Each sample contains 4 to 16 files named `img0.tif`, `img1.tif`, and so on, with no gaps in the sequence.

**Fixed and variable files.** The same folder may contain both forms.

```
{
  "taco:structure": [
    "before/B02.tif",
    "before/B03.tif",
    "before/B04.tif",
    "before/mask*[1,5].tif"
  ]
}
```

The identifiers inside `before/` are `B02.tif`, `B03.tif`, `B04.tif`, and `mask`. Each is unique.

### 5.3. Reserved Characters

TACO reserves three tokens so that paths, levels, and metadata fields can be mapped without ambiguity.

| Token | Purpose | Rule |
| --- | --- | --- |
| `:` | Separates a namespace from a field name | Every user field MUST contain exactly one. It MUST NOT appear in folder names, file names, or level keys. |
| `/` | Separates path and level segments | It MAY appear in complete structure paths and level keys. It MUST NOT appear inside a folder or file name. |
| `__` | Replaces `/` in Parquet filenames | It MUST NOT appear in folder names, file names, namespaces, or metadata field names. |

For example, the level `children/before` is stored in `children__before.parquet`.

Folder and file names MUST NOT contain `<`, `>`, `:`, `"`, `\`, `|`, or `?`, and MUST NOT end with a space or period. The characters `*`, `[`, and `]` are reserved for variable sequence declarations.

### 5.4. Metadata

Tabular metadata describes the sample and the nodes inside it. A node is either a folder or a file. Collection metadata is global to the dataset and is defined separately in Section 5.5.

The tabular metadata schema is written to `taco:metadata`. Each level maps qualified field names to their declarations.

Every field MUST declare the following properties. Its type MUST be representable in Apache Parquet.

| Property | Type | Meaning |
| --- | --- | --- |
| `type` | string | Canonical Arrow type |
| `nullable` | boolean | Whether the stored column may contain null values |
| `description` | string | Human-readable field description; MAY be empty |

| Type family | Canonical forms |
| --- | --- |
| Scalar | `bool`, signed and unsigned integers from 8 to 64 bits, `float16`, `float`, `double`, `string`, `large_string`, `binary`, `large_binary`, `date32`, `date64` |
| Temporal | `timestamp[unit]`, `timestamp[unit, timezone]`, `time32[unit]`, `time64[unit]`, `duration[unit]` |
| Nested and fixed | `list<type>`, `large_list<type>`, `fixed_size_list<type, size>`, `fixed_size_binary[size]`, `struct<name: type, ...>`, `map<key, value>`, `decimal128(precision, scale)`, `decimal256(precision, scale)` |

Integer names are `int8`, `int16`, `int32`, `int64`, `uint8`, `uint16`, `uint32`, and `uint64`. Time units are `s`, `ms`, `us`, and `ns`.

Nested values are non-nullable by default. A `?` suffix makes a nested value nullable. The top-level type MUST NOT use `?` because top-level nullability is declared by the field's `nullable` property. Map keys MUST NOT be nullable.

For example, `list<int64>` does not allow null list items, while `list<int64?>` does. The following type allows null values for `minimum` but not for `valid_count`.

```
struct<minimum: double?, valid_count: uint64>
```

The same rule applies recursively.

```
list<struct<name: string, score: double?>?>
```

This type allows a null struct inside the list. A non-null struct still requires a non-null `name`, while `score` may be null.

A level identifies what each row describes.

| Level | Rows |
| --- | --- |
| `sample` | One row per sample |
| `children` | One row per direct folder or file below a sample |
| `children/<folder-path>` | One row per direct folder or file below that folder in every sample |

The text after `children/` is the complete folder path from `taco:structure`. For example, `children/before` contains one row for every file directly inside each `before/` folder. Every contract has the `sample` and `children` levels; additional child levels are derived from folders in the structure.

`taco:metadata` MUST include `sample` and every child level implied by the structure. A level with no user fields is stored as an empty object.

Each level becomes one Parquet table. Rows from all samples are stored together and linked to their parents with `internal:parent_id`.

Every user column MUST follow the type and nullability declared in the contract. A non-empty description MUST be stored in the Arrow field metadata under `description`.

#### Namespaces

Every user field MUST use the form `namespace:field`. Namespaces MUST match `[a-z][a-z0-9_]*`. Field names MUST be non-empty and MUST NOT contain `:`, `/`, or `__`. The qualified name MUST be unique within its level, ignoring letter case, because SQL does not distinguish `ml:split` from `ml:Split`.

The same namespace MAY appear at multiple levels and MAY use a different schema at each level. The `internal`, `taco`, and `cozip` namespaces are reserved. Producers MUST NOT create fields in these namespaces.

The column names `cozip:location`, `taco:location`, and `taco:sample_index` are reserved for readers. A producer MUST NOT store them in a Parquet file under `METADATA/`.

A reader MUST calculate locations from the payload offset, size, and containing file. It MUST ignore or remove stored values that use any reserved name.

On disk, a namespace only qualifies a column name. It does not create a nested struct or identify a Python class. Contracts are equivalent when their structure, levels, qualified fields, types, nullability, and descriptions are the same.

#### Spatial and temporal profiles

TACO defines three metadata profiles built from the fields of a STAC Item and its projection extension. `Temporal` records when a sample was observed, `Spatial` where it is, and `STAC` both.

A metadata level MUST choose at most one profile. A profile is a group of tabular fields, not a serialized STAC Item. Its fields keep their STAC names, except that the `proj:` prefix becomes `proj_` so that every column has a single namespace. For example, STAC `proj:code` is stored as `stac:proj_code`.

| Profile | Producer inputs | Writer outputs |
| --- | --- | --- |
| `temporal` | `datetime`, or `start_datetime` and `end_datetime` | None |
| `spatial` | `geometry`, or `proj_code`, `proj_shape`, and `proj_transform` | `geometry`, `bbox`, `centroid` |
| `stac` | The inputs of both | `geometry`, `bbox`, `centroid` |

Every profile MUST use the following canonical field declarations. A field is present in each profile listed under Applies to. When the complete profile group is optional, every stored column in that group is nullable. Its fields and types remain canonical.

| Field | Applies to | Type | Nullable | STAC field | Meaning |
| --- | --- | --- | --- | --- | --- |
| `geometry` | `spatial`, `stac` | `binary` | No | `geometry` | Footprint in EPSG:4326 as WKB |
| `bbox` | `spatial`, `stac` | `list<double>` | No | `bbox` | `[west, south, east, north]` of the footprint |
| `centroid` | `spatial`, `stac` | `binary` | No | None | Center of the sample as a WKB Point in EPSG:4326 |
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

**Time.** Every row MUST have a `datetime`, or both a `start_datetime` and an `end_datetime`. `start_datetime` and `end_datetime` MUST be given together, MUST NOT be reversed, and bound an inclusive range. A row MAY also carry a `datetime` inside its range. An observation without a single acquisition instant, such as a DEM or an annual composite, uses a range.

**Footprint.** `geometry` MUST be a non-empty, valid, two-dimensional WKB geometry with longitude and latitude coordinates in EPSG:4326. It MUST NOT be a GeometryCollection. A footprint that crosses the antimeridian MUST be split into parts at 180 degrees, as RFC 7946 requires. Therefore no edge may span more than 180 degrees of longitude, except an edge whose two ends lie on the antimeridian or an edge along a pole.

**Bounding box.** `bbox` holds `[west, south, east, north]`. `south` and `north` are the latitude bounds of `geometry`. `west` and `east` bound the narrowest longitude interval that covers every part of `geometry`. When that interval crosses the antimeridian, `west` is greater than `east`. When two intervals are equally narrow, the one that does not cross is used.

**Grid.** `proj_code`, `proj_shape`, and `proj_transform` MUST be given together or not at all. `proj_code` has the form `AUTHORITY:CODE`, such as `EPSG:32718`. `proj_shape` holds the positive height and width of the grid in pixels, in that order. `proj_transform` holds six finite coefficients `[a, b, c, d, e, f]` with a non-zero `a*e - b*d`. They map the corner of a pixel to CRS coordinates:

```
x = a * column + b * row + c
y = d * column + e * row + f
```

This is the order of STAC `proj:transform` and of rasterio, not the order of the GDAL geotransform. A grid in `EPSG:4326` uses longitude as `x`.

**Writer outputs.** A row MUST supply `geometry`, a complete grid, or both. When a row supplies only a grid, the writer approximates its edges with 16 segments per side reprojected to EPSG:4326. The sampled boundary is retained because straight edges in the source CRS can curve after reprojection. A grid across the antimeridian is split at 180 degrees. A grid around a pole retains its sampled boundary and closes through the enclosed pole. This is a fixed-resolution approximation; a producer needing a more precise footprint supplies `geometry`. When a row supplies `geometry`, the writer keeps it; it MAY differ from the grid, for example to trace the valid data. The writer computes `bbox` from `geometry`. A `bbox` supplied by the producer MUST equal the computed value.

**Centroid.** `centroid` is the point that stands for the sample, for example to assign a grid cell. When the row has a grid, the writer takes the center of the grid, at `column = width / 2` and `row = height / 2`, in its CRS and reprojects that single point, so `centroid` does not depend on how `geometry` approximates the grid. Without a grid, it is the centroid of `geometry`, with the parts of a footprint split at the antimeridian joined across it first. A producer MAY supply `centroid` instead. It is not a STAC field, so an exported STAC Item omits it.

The built-in extensions `spatial=taco.extensions.Spatial()` and `stac=taco.extensions.STAC()` compute the outputs and MUST use their canonical namespaces. Temporal computes nothing, so it is declared as a model, as in `temporal=taco.metadata.sample.Temporal`. A level MAY bind the Spatial or STAC model without its extension; the producer then supplies `geometry` and `bbox`.

Inputs use the matching model from `taco.metadata.sample` or `taco.metadata.folder`.

#### Writer-time extensions

A writer-time extension receives validated inputs and computes columns during `run()`. An extension may require a producer input or the output of another extension at the same level.

The writer MUST resolve extension dependencies without relying on declaration order. A cycle, a missing requirement, or a duplicate output column makes the active writer contract invalid.

The dependency graph and operational configuration exist only while writing. Parameters used only to control execution, such as batch size, worker count, credentials, and temporary paths, MUST NOT be stored in `COLLECTION.json`.

An extension parameter that changes how a stored column is calculated or interpreted MUST be stored as collection metadata in the extension's namespace. The writer MUST add this metadata automatically and MUST reject an append when its value differs from the existing collection.

For example, `MajorTOM(dist_km=100)` changes the meaning of `majortom:code`, so the collection stores the distance:

```
{
  "majortom:dist_km": 100
}
```

An append using `MajorTOM(dist_km=100)` is compatible. An append using `MajorTOM(dist_km=50)` MUST fail because it would place codes calculated with two grid sizes in the same column.

Every produced column MUST appear in `taco:metadata` with its final type, nullability, and description.

#### Rumi extension

The Rumi extension operates on one local `.rumi` asset per row. It stores `rumi:header` unless configured with `header=False`, and `rumi:stats` when configured with `stats=True`. At least one of the two MUST be enabled. It MUST obtain `rumi:header` from `rumi.info(source=asset_path).header`. Producers MUST NOT construct this value themselves.

The header is stored as Parquet `binary`. A reader can use it for selective access without parsing the payload first.

When statistics are enabled, the extension also stores `rumi:stats` as one list entry per band with the following non-null top-level type.

```
list<struct<minimum: double?, maximum: double?, mean: double?, stddev: double?, valid_count: int64, nodata_count: int64>>
```

Cube statistics combine the time and spatial axes for each band. Non-finite values and the configured nodata sentinel are excluded.

#### Examples

**CloudSEN12.**

```
{
  "taco:metadata": {
    "sample": {
      "stac:geometry": {"type": "binary", "nullable": false, "description": "Footprint in EPSG:4326 as WKB"},
      "stac:bbox": {"type": "list<double>", "nullable": false, "description": "Footprint bounds [west, south, east, north]"},
      "stac:centroid": {"type": "binary", "nullable": false, "description": "Center point in EPSG:4326 (WKB)"},
      "stac:datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Acquisition time"},
      "stac:start_datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Acquisition start, inclusive"},
      "stac:end_datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Acquisition end, inclusive"},
      "stac:proj_code": {"type": "string", "nullable": true, "description": "CRS of the grid"},
      "stac:proj_shape": {"type": "list<int64>", "nullable": true, "description": "Grid size as [height, width]"},
      "stac:proj_transform": {"type": "list<double>", "nullable": true, "description": "Affine transform of the grid"},
      "quality:cloud_cover": {"type": "double", "nullable": true, "description": "Cloud cover percentage (0-100)"},
      "ml:split": {"type": "string", "nullable": false, "description": "Dataset split (train, val, test)"}
    },
    "children": {}
  }
}
```

`sample` contains one row per sample. `children` contains one row for each of its three files.

**Change detection.**

```
{
  "taco:metadata": {
    "sample": {
      "stac:geometry": {"type": "binary", "nullable": false, "description": "Sample footprint (WKB)"},
      "stac:bbox": {"type": "list<double>", "nullable": false, "description": "Sample footprint bounds"},
      "stac:centroid": {"type": "binary", "nullable": false, "description": "Sample center point (WKB)"},
      "stac:datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Sample acquisition time"},
      "stac:start_datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "First acquisition of the pair"},
      "stac:end_datetime": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Last acquisition of the pair"},
      "stac:proj_code": {"type": "string", "nullable": true, "description": "CRS of the grid"},
      "stac:proj_shape": {"type": "list<int64>", "nullable": true, "description": "Grid size as [height, width]"},
      "stac:proj_transform": {"type": "list<double>", "nullable": true, "description": "Affine transform of the grid"},
      "ml:split": {"type": "string", "nullable": false, "description": "Dataset split"},
      "change:ratio": {"type": "double", "nullable": false, "description": "Percentage of changed pixels"}
    },
    "children": {
      "acquisition:time": {"type": "timestamp[us, UTC]", "nullable": true, "description": "Acquisition timestamp"},
      "quality:cloud_cover": {"type": "double", "nullable": true, "description": "Cloud cover percentage"},
      "eo:sensor": {"type": "string", "nullable": true, "description": "Sensor name"}
    },
    "children/before": {},
    "children/after": {}
  }
}
```

`sample` contains one row per sample. `children` contains rows for `before`, `after`, and `change_map.tif`. Acquisition fields are null for `change_map.tif`. The two folder levels contain one row for each band file.

### 5.5. Collection

`COLLECTION.json` contains the dataset description, contract, and collection metadata. Within a FOLDER or ZIP partition, each sample has a zero-based position. The `id` column defined in Section 7.2 is its logical identifier.

For this specification, `taco:version` MUST equal `3.0.0`. The `id` and `description` MUST be non-empty. The `licenses` and `providers` lists MUST each contain at least one entry. `tasks` MAY be omitted; when present it MUST contain at least one entry.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `taco:version` | string | MUST | TACO specification version |
| `id` | string | MUST | Dataset identifier |
| `description` | string | MUST | Dataset description |
| `licenses` | list[string] | MUST | License identifiers (e.g. `CC-BY-4.0`) |
| `providers` | list[object] | MUST | Dataset providers with name and roles |
| `tasks` | list[string] | MAY | ML task types (e.g. `segmentation`, `classification`) |
| `taco:structure` | list[string] | MUST | Non-empty sample structure |
| `taco:metadata` | object | MUST | Tabular metadata schema |
| `title` | string | MAY | Human-readable title |
| `curators` | list[object] | MAY | Dataset curators |
| `keywords` | list[string] | MAY | Keywords for discovery |
| `extent` | object | Spatial profiles | Writer-generated spatial and optional temporal summary |
| `taco:sources` | object | TACOCAT | Source ZIP partitions |

A provider MUST have a non-empty `name`. It MAY have `roles`, `url`, and `links`. A curator MUST have a `name` or `organization` and MAY have `email` and `role`.

An extent contains `spatial` and MAY contain `temporal`. `spatial` is `[west, south, east, north]` in EPSG:4326. A value where west is greater than east crosses the antimeridian. `temporal` is either null or `[start, end]` using ISO 8601 timestamps in UTC.

The writer produces `extent` from the shallowest metadata level that contains a spatial profile. If no level contains one, the writer MUST omit `extent`.

The spatial interval covers the `bbox` values at that level. `south` and `north` are their extreme latitudes. `west` and `east` bound the narrowest longitude interval that covers every box, under the rule for `bbox` in Section 5.4.

For STAC, the temporal interval starts at the earliest `start_datetime` or `datetime` and ends at the latest `end_datetime` or `datetime`.

Each ZIP partition MUST summarize only its own rows. TACOCAT stores each partition extent in `taco:sources` and uses their union as its collection extent. A FOLDER append MUST summarize both the existing and appended rows.

#### Collection metadata

Collection metadata is stored directly in `COLLECTION.json` as qualified fields. It uses the same `namespace:field` rule as tabular metadata but is not part of `taco:metadata` and does not create Parquet columns.

```
{
  "labels:classes": ["clear", "cloud", "shadow"],
  "labels:description": "Cloud mask classes",
  "optical:sensor": "Sentinel-2"
}
```

The Python writer takes these values as groups, one keyword argument of `Collection` per namespace, but the serialized form remains flat and language-independent. Collection groups and sample groups are separate. A collection group MUST NOT be attached to a sample, folder, or asset.

Collection metadata values MUST be valid JSON. Non-finite numbers such as NaN and Infinity are not allowed.

Unknown unqualified fields are invalid. Unknown fields in a valid user namespace MAY be preserved. Unknown fields in the reserved `taco`, `internal`, or `cozip` namespaces MUST be rejected.

### 5.6. Properties

A stored sample directory and a structure path identify every file. In a FOLDER, they produce a path such as `DATA/42/before/B02.tif`. In a ZIP, Parquet metadata provides the offset and size used in the VSI path.

Any subset of samples may use the same contract. Each partition assigns its own local sample indices.

Partitions with the same contract and collection metadata may be combined. A physical merge MUST either reindex every row and parent reference or retain the source partition as part of row identity.

## 6. Dataset Identity and Mutability

The `id` in `COLLECTION.json` identifies the dataset. Corrections and appended samples MAY keep the same `id` when the dataset's purpose, contract, and field semantics remain unchanged. A new `id` is required when any of those change. For example, changing a cloud segmentation dataset into a land-cover dataset, adding a metadata field, or reorganizing its sample structure creates a new dataset.

### 6.1. Container Mode and Mutability

FOLDER containers support appending samples. New data is added under `DATA/`, new rows are added to `METADATA/`, and `COLLECTION.json` is updated. An append MUST preserve the contract and the meaning of every existing field.

ZIP containers are immutable and must be rebuilt after any change. On CDC-aware platforms, rebuilding a ZIP may transfer only the chunks that changed.

Producers SHOULD build datasets as FOLDER containers and publish them as ZIP containers. Conversion preserves the contract and logical content, but ZIP metadata adds the byte offsets and sizes required for random access.

## 7. Physical Layer

This section defines how a TACO dataset is stored.

### 7.1. Directory Structure

Every FOLDER or ZIP dataset MUST contain the logical paths `DATA/`, `METADATA/`, and `COLLECTION.json`. ZIP archives store these as entry prefixes and MUST NOT contain explicit directory entries.

```
dataset/
├── DATA/
│   ├── 0/
│   │   ├── before/
│   │   │   ├── B02.tif
│   │   │   ├── B03.tif
│   │   │   └── B04.tif
│   │   ├── after/
│   │   │   ├── B02.tif
│   │   │   ├── B03.tif
│   │   │   └── B04.tif
│   │   └── change_map.tif
│   ├── 1/
│   │   └── ...
│   └── ...
├── METADATA/
│   ├── sample.parquet
│   ├── children.parquet
│   ├── children__before.parquet
│   └── children__after.parquet
└── COLLECTION.json
```

`DATA/` contains the data files. Each sample MUST use a directory named by its integer index. Every file is stored as `DATA/<idx>/<structure-path>` and the directory contents MUST follow the contract exactly.

Every dataset MUST contain at least one sample, and every sample MUST contain at least one file declared by its structure. No other files are allowed under `DATA/`. These rules guarantee that `DATA/` is represented by at least one stored path in both FOLDER and ZIP containers.

Every stored data file MUST contain at least one byte. This rule applies to both FOLDER and ZIP containers.

`METADATA/` contains one Parquet file for each key in `taco:metadata`. The filename replaces every `/` in the level name with `__`. Each Parquet schema MUST store its original level name as `taco:level` metadata.

`COLLECTION.json` contains the dataset description and contract defined in Section 5.5.

TACOCAT uses the separate layout defined in Section 7.5 and does not contain `DATA/` or `METADATA/` directories.

### 7.2. Internal Columns

TACO adds columns for identity, row relationships and data access. `id` is unqualified because the specification defines it; the rest use the reserved `internal:` namespace. Users MUST NOT create either.

| Column | Type | Nullable | Present in |
| --- | --- | --- | --- |
| `id` | `string` | No | `sample.parquet` only |
| `internal:current_id` | `uint64` | No | All Parquets |
| `internal:parent_id` | `uint64` | No | All except sample |
| `internal:relative_path` | `string` | No | All Parquets |
| `internal:offset` | `uint64` | Yes | ZIP and TACOCAT levels containing files |
| `internal:size` | `uint64` | Yes | ZIP and TACOCAT levels containing files |
| `internal:source_file` | `string` | No | TACOCAT only |

`id` is the logical identity supplied by the producer. It MUST be non-empty and unique across the dataset, including every TACOCAT partition. Consolidation and subsetting MUST preserve it. Use `id` to cite a sample or join datasets that describe the same samples.

In every container, `internal:current_id` MUST equal the zero-based row position in its Parquet file. Sample rows follow writer input order in FOLDER and ZIP datasets. Child rows are grouped by parent order and follow the structure order defined in Section 5.2.

`internal:parent_id` refers to `internal:current_id` in the parent level. A join between two levels therefore uses `child.internal:parent_id = parent.internal:current_id`.

`internal:relative_path` is relative to `DATA/` in the file containing the payload. A FOLDER or ZIP sample row uses `<idx>`, and a child row uses `<idx>/<structure-path>`. TACOCAT retains these source-relative values after reindexing.

ZIP and TACOCAT metadata MUST include `internal:offset` and `internal:size` at every child level containing files. These values are null for folder rows. `sample.parquet` MUST NOT contain these columns because its rows represent sample roots rather than files.

`internal:offset` is the payload offset measured from byte 0 of the source ZIP. `internal:size` is the payload length in bytes.

TACOCAT reassigns `internal:current_id` after combining its source partitions. The new identifiers are global within each consolidated Parquet file. It also rewrites `internal:parent_id` to refer to the consolidated parent level.

Parent joins use only `internal:parent_id` and `internal:current_id`. The values of `internal:source_file` and `id` do not change.

Readers expose a generated `taco:sample_index` column for public queries. For one FOLDER, ZIP, or TACOCAT container, its value matches `internal:current_id` in `sample.parquet`. For a source list, the reader assigns it across all sources in source order and then row order. `taco:sample_index` is not stored and MUST NOT be used as a persistent sample identity. Use `id` for stable identity.

Readers use these columns to build VSI paths. ZIP paths use the form `/vsisubfile/{offset}_{size},{zip_path}`. FOLDER files use `DATA/{idx}/{structure_path}` and do not require a Parquet lookup for the data path.

### 7.3. ZIP Container

A ZIP container packages one dataset or partition in a `.zip` archive. It MUST satisfy the complete TACO profile in Section 14 of the CoZIP 1.1.0 specification. Its CoZIP index declares profile 2 and contains offsets to `COLLECTION.json` and every Parquet file in `METADATA/`.

All priority files MUST form the final contiguous entry block before the Central Directory. Every archive entry MUST use STORE mode, and every filename MUST satisfy the CoZIP path rules.

The writer MUST use the `.zip` extension. Readers MUST identify TACO archives by profile 2 in the CoZIP index, not by the filename.

ZIP containers are immutable. Any update requires rebuilding the archive.

### 7.4. FOLDER Container

A FOLDER container is a plain directory with the logical layout from Section 7.1. It uses the same contract and data paths as ZIP, but its Parquet files do not contain ZIP offsets.

New samples can be appended without rebuilding existing data, making FOLDER suitable for incremental datasets.

The writer MUST publish an append as one completed update. If the operation fails, the previously published dataset MUST remain unchanged.

### 7.5. TACOCAT

TACOCAT makes several ZIP partitions queryable as one collection. It contains consolidated metadata and references the original ZIP files for data access.

```
.tacocat/
├── sample.parquet
├── children.parquet
├── children__before.parquet
├── children__after.parquet
└── COLLECTION.json
```

A reader MUST accept either the `.tacocat/` directory or its parent dataset directory. For example, when `/datasets/clouds/` has no `COLLECTION.json`, the reader tries `/datasets/clouds/.tacocat/COLLECTION.json`.

Each Parquet file combines the corresponding tables from the source ZIPs. Partitions follow their order in `taco:sources.partitions`. Rows within a partition keep their original order.

The consolidator MUST assign `internal:current_id` again from zero in every combined table. It MUST also rewrite `internal:parent_id` using the combined parent table. `internal:source_file` identifies the source ZIP of each row.

For example, two source sample tables may both start at zero.

```
source_file | current_id | id
a.zip       | 0          | lima-01
a.zip       | 1          | lima-02
b.zip       | 0          | cusco-01
```

TACOCAT stores them with global identifiers.

```
source_file | current_id | id
a.zip       | 0          | lima-01
a.zip       | 1          | lima-02
b.zip       | 2          | cusco-01
```

Child identifiers and parent references are reassigned in the same way.

```
source_file | current_id | parent_id | relative_path
a.zip       | 0          | 0         | 0/image.tif
a.zip       | 1          | 1         | 1/image.tif
b.zip       | 2          | 2         | 0/image.tif
```

`internal:relative_path` remains relative to the source ZIP, so sample row 2 in the consolidated table still references `0/image.tif` inside `b.zip`.

A source path is resolved relative to the directory containing `.tacocat/`. It MUST be a normalized relative POSIX path ending in `.zip`.

The path MUST NOT contain an empty component, `.`, `..`, a leading slash, a trailing slash, or a backslash. To read a file, the reader combines the source path with `internal:offset` and `internal:size`.

For example, this source stays below the catalog directory and is valid:

```
{"file": "partitions/europe.zip", "samples": 100000}
```

The following sources are invalid:

```
{"file": "../europe.zip", "samples": 100000}
{"file": "/data/europe.zip", "samples": 100000}
{"file": "partitions\\europe.zip", "samples": 100000}
```

The first escapes the catalog directory, the second is absolute, and the third uses a platform-specific separator.

TACOCAT consolidation MUST verify that every partition has the same `id`, contract, and collection metadata. The only fields that may differ are `extent` and `taco:sources`. The consolidated extent is the union of the partition extents.

`taco:sources` records each partition, its sample count, and its extent. Readers can use this information to skip irrelevant ZIP files.

Every source path MUST be unique and MUST match `internal:source_file` in the consolidated Parquet rows. `taco:sources` MUST appear in TACOCAT and MUST NOT appear in a FOLDER or ZIP dataset.

`samples` is the total number of samples and MUST equal the sum of the partition counts. Each entry in `partitions` MUST contain `file` and `samples`. It MUST also copy `spatial` and `temporal` when the source partition declares them.

```
{
  "taco:sources": {
    "samples": 300000,
    "partitions": [
      {"file": "europe.zip", "samples": 100000, "spatial": [-10, 30, 45, 70], "temporal": ["2023-01-01T00:00:00Z", "2023-12-31T23:59:59Z"]},
      {"file": "asia.zip", "samples": 100000, "spatial": [60, -10, 150, 55], "temporal": ["2023-01-01T00:00:00Z", "2023-12-31T23:59:59Z"]},
      {"file": "americas.zip", "samples": 100000, "spatial": [-170, -55, -35, 75], "temporal": ["2023-01-01T00:00:00Z", "2023-12-31T23:59:59Z"]}
    ]
  }
}
```

### 7.6. Validation

A conforming dataset MUST satisfy every rule in this specification. `taco.validate()` MUST check the complete physical container and report each violation. A reader MUST reject any violation it encounters, but it does not need to read every payload before serving an unrelated query.

Complete validation MUST check the following conditions.

1. `COLLECTION.json` is valid and declares a supported `taco:version`, a valid collection, and a complete contract.

2. `METADATA/` contains exactly one Parquet file for every contract level and no unexpected entries. Each file carries the matching `taco:level` schema metadata.

3. Every Parquet schema contains exactly the user columns declared for its level and the internal columns required by its container. Types, top-level nullability, nested nullability, and descriptions MUST match the contract. A non-empty description is stored in Arrow field metadata under `description`.

4. `internal:current_id` equals the row position, every `internal:parent_id` resolves to a row in the parent level, and every `internal:relative_path` matches the sample structure.

5. Every sample `id` is non-empty and unique. Every sample contains all required fixed files and a valid number of contiguous files for every variable sequence.

6. `DATA/` contains every declared file and no undeclared file. Every stored data file contains at least one byte.

7. ZIP entries satisfy the CoZIP TACO profile. Every `internal:offset` and `internal:size` pair matches the payload boundary and size of its declared file.

8. TACOCAT source paths, sample counts, global identifiers, parent references, extents, and `internal:source_file` values match `taco:sources` and the referenced ZIP partitions.

9. Every row of a Spatial, Temporal, or STAC profile follows the rules for time, footprint, bounding box, and grid in Section 5.4. A row where an optional profile group is absent as a whole is exempt.

For example, this contract declaration requires an `int64` Parquet column.

```
{
  "quality:score": {
    "type": "int64",
    "nullable": false,
    "description": "Quality score"
  }
}
```

A physical `int32` column is invalid even when every stored value fits in 32 bits. A nullable `int64` column is also invalid because its schema does not match the contract.

## 8. API Layer

Every TACO dataset MUST be created through the public API of the `taco` package. Alternative writer implementations are not permitted, even when they reproduce the same physical layout.

Readers, validators, and inspection tools MAY be implemented in any language. For example, a Rust reader that interprets a TACO dataset according to this specification is valid. A Rust library that creates datasets MUST NOT claim to be a TACO writer.

### 8.1. taco (Writer)

`taco.open_writer()` creates both ZIP and FOLDER containers. A path ending in `.zip` selects ZIP. Any other path selects FOLDER.

#### Data objects

The writer uses the following immutable data objects.

| Object | Purpose |
| --- | --- |
| `Contract` | Defines the structure and tabular metadata schema |
| `Collection` | Describes the dataset |
| `Sample` | Contains one sample |
| `Folder` | Attaches metadata to a folder declared by the contract |
| `Asset` | Points to one source file |
| `Level` | Associates namespaces with models or writer-time extensions; `Contract(metadata=[...])` takes a list of them |
| `Metadata` | Carries values for a sample, folder, or asset |

Metadata for the complete dataset is not a separate object. It is passed to `Collection` as groups, described under Collection metadata below.

#### Metadata schema

Built-in metadata groups are separated by scope.

| Scope | Module |
| --- | --- |
| Sample | `taco.metadata.sample` |
| Folder | `taco.metadata.folder` |
| Asset | `taco.metadata.asset` |
| Collection | `taco.metadata.collection` |

Writer-time operations live under `taco.extensions`. A dataset may use the built-in groups or define Pydantic models and `taco.Extension` subclasses.

The writer rejects a built-in group used outside its declared scope.

```
import taco
from pydantic import BaseModel, Field


class ML(BaseModel):
    split: str = Field(description="Dataset split")


contract = taco.Contract(
    structure=["image.tif", "label.tif"],
    metadata=[
        taco.Level(
            "sample",
            stac=taco.extensions.STAC(),
            ml=ML,
            majortom=taco.extensions.MajorTOM(dist_km=100),
        ),
    ],
)
```

The keyword passed to `Level` becomes the namespace. A Pydantic model defines fields, types, nullability, and descriptions. An extension also declares its required and produced fields. The writer stores qualified columns such as `stac:geometry`, `stac:bbox`, and `majortom:code`.

Spatial, Temporal, and STAC MUST use their canonical namespaces.

A model assigned directly to a level is required on every row. `Model | None` allows the complete group to be absent on some rows and makes its stored columns nullable. Within a model, `value: T | None` makes one field nullable. `Field(description=...)` supplies its description. Exact Arrow types may use `Annotated`.

```
from datetime import datetime
from typing import Annotated

import pyarrow as pa

Float32 = Annotated[float, pa.float32()]
TimestampUTC = Annotated[datetime, pa.timestamp("us", tz="UTC")]
```

The serialized contract contains the resulting columns, not Python class names, import paths, defaults, or validators.

#### Collection metadata

Pass collection metadata as keyword groups. Each group accepts a mapping or a Pydantic model that serializes to a JSON object. For example, `source={"mission": "Sentinel-2"}` writes `"source:mission": "Sentinel-2"` to `COLLECTION.json`. The models in `taco.metadata.collection` provide common fields and validation.

```
collection = taco.Collection(
    contract=contract,
    id="cloud-segmentation",
    description="Cloud segmentation dataset",
    licenses=["CC-BY-4.0"],
    providers=[{"name": "CSIC", "roles": ["producer"]}],
    labels=taco.metadata.collection.Labels(
        classes=["clear", "cloud", "shadow"],
        description="Cloud mask classes",
    ),
    source={"mission": "Sentinel-2", "level": "L1C"},
)
```

Groups must be non-empty, use valid qualified field names, and contain JSON values. Models scoped to samples, folders, or assets are rejected, as are values that conflict with an active extension. Validation failures raise `CollectionError`.

Group names cannot be `metadata` or a named parameter of `Collection`. Readers still preserve those namespaces when they occur in a file. `Collection.replace(poi={...})` replaces the entire `poi` group; `poi=None` removes it. `taco.export()` accepts the same overrides.

`collection.metadata` holds the groups as JSON values. `to_dict()` returns an independent copy, so editing its nested lists or dictionaries does not change the collection. Collection groups are validated at construction and stored only in `COLLECTION.json`. The writer adds `taco:version` automatically.

#### Collection summaries

Spatial produces the spatial part of `extent` from the bounding boxes. STAC additionally produces its temporal part. Temporal has no spatial coverage and therefore does not synthesize a collection `extent`.

Summaries run independently for every output partition. They consume metadata in batches and retain only the values needed for the summary. They do not keep the complete metadata table in memory.

#### Sample metadata

Metadata is attached to the object it describes. `Sample.metadata` describes the sample, `Folder.metadata` describes a folder, and `Asset.metadata` describes a file. The writer assigns each object to the correct metadata level.

Assets are passed as a list. In a flat structure, the contract path is inferred when the source filename matches it, so the name is not repeated. The `path` argument is required for nested paths or when the source name differs from the contract.

```
sample = taco.Sample(
    id="lima-0001",
    metadata=taco.Metadata(
        stac=taco.metadata.sample.STAC(...),
        ml=ML(split="train"),
    ),
    assets=[
        taco.Asset(Path("/data/image.tif")),
        taco.Asset(Path("/data/label.tif")),
    ],
)
```

Folders are inferred from the contract and need no runtime object unless they carry metadata. When they do, the sample names them once.

```
sample = taco.Sample(
    id="lima-0001",
    folders=[taco.Folder("before", metadata=...)],
    assets=[...],
)
```

#### Writer-time extensions

An extension declares the columns it requires and produces. It may also declare a Pydantic model for values supplied by the producer. The contract is invalid when a required column is missing.

For example, Spatial receives a grid and produces `spatial:geometry`, `spatial:bbox`, and `spatial:centroid`. `MajorTOM(centroid="spatial:centroid")` may require that column and produce `majortom:code`, the MajorTOM cell that contains the centroid.

The writer computes extension outputs from batches of validated metadata during `run()`. Each context also contains the local asset associated with every row, allowing format extensions to inspect payloads without asking producers to duplicate file metadata.

`taco.extensions.Rumi(stats=True)` requires a local `.rumi` asset. It produces the canonical binary `rumi:header` and the per-band `rumi:stats`; `header=False` omits the header. `taco.extensions.GeoEnrich` attaches selected environmental variables through one of two backends. The default `majortom-index` backend joins a 10 km MajorTOM code against the public MajorTOM index on Source Cooperative without requiring an Earth Engine account; the explicitly selected `earthengine` backend samples a configurable centroid. The index backend and source URL are stored as collection metadata.

Extension dependencies and operational settings remain in the active Python contract while writing. Semantic parameters are stored as collection metadata as defined in Section 5.4. The persisted contract contains only the resulting structure and metadata schema.

#### Writer lifecycle

`add()` and `extend()` validate and stage samples. They do not modify the final dataset.

`run()` closes the input, computes derived metadata and ZIP offsets, writes each Parquet file once, and publishes the result. If a build fails, an existing dataset MUST remain unchanged.

Leaving the context manager only removes temporary files. It does not call `run()`. Calling `run()` again after success has no effect.

FOLDER containers may use `append=True`. ZIP containers may be partitioned. The writer builds every partition and the TACOCAT before publishing them together. If publication fails, it restores the previous files.

#### Export

`taco.export()` writes a subset of an existing dataset through the same writer. Its required `sql` argument may query `dataset`, `sample`, or any declared metadata level. Every relation carries `taco:sample_index` while this query runs, and the result MUST retain that column.

The query selects samples, not individual files. If any returned row belongs to a sample, the export includes that sample's complete structure and metadata. Duplicate rows select the sample once. Values of `taco:sample_index` that do not belong to the source are ignored.

The output keeps the source contract, identity, licenses, providers, tasks, and collection metadata unless the caller replaces collection fields. Selected samples keep their `id` and receive new indices from zero in source order. The writer recalculates `extent` and removes `taco:sources`.

For example, this query examines only the `after` level. Every matching sample is still exported with its `before`, `after`, and any other files required by the structure.

```
source = "change-detection.zip"
taco.export(
    source,
    "clear-after.zip",
    sql='SELECT * FROM "children/after" WHERE "quality:cloud_cover" < 10',
)
```

Collection fields such as `id`, `title`, and `description` are optional. Omitted fields keep their source values. `overwrite=True` replaces an existing TACO output.

#### Example

```
from datetime import datetime, timezone
from pathlib import Path

import taco
from pydantic import BaseModel, Field


class ML(BaseModel):
    split: str = Field(description="Dataset split")


contract = taco.Contract(
    structure=["image.tif", "label.tif"],
    metadata=[
        taco.Level(
            "sample",
            stac=taco.extensions.STAC(),
            ml=ML,
            majortom=taco.extensions.MajorTOM(dist_km=100),
        ),
    ],
)

collection = taco.Collection(
    contract=contract,
    id="cloud-segmentation",
    description="Cloud segmentation dataset",
    licenses=["CC-BY-4.0"],
    providers=[{"name": "CSIC", "roles": ["producer"]}],
    tasks=["segmentation"],
    labels=taco.metadata.collection.Labels(
        classes=["clear", "cloud"],
        description="Cloud mask classes",
    ),
)

sample = taco.Sample(
    id="lima-0001",
    metadata=taco.Metadata(
        stac=taco.metadata.sample.STAC(
            proj_code="EPSG:4326",
            proj_shape=(256, 256),
            proj_transform=(0.1 / 256, 0, -76.55, 0, -0.1 / 256, -9.15),
            datetime=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ),
        ml=ML(split="train"),
    ),
    assets=[taco.Asset(Path("/data/image.tif")), taco.Asset(Path("/data/label.tif"))],
)

with taco.open_writer(collection, "clouds.zip") as writer:
    writer.add(sample)
    writer.run()
```

### 8.2. Reader

The TACO core is the reader. Every binding loads this core and MUST expose the same operations, arguments, relations, validation rules, column order, and logical results.

The core detects ZIP, FOLDER, or TACOCAT from the path. It reads `COLLECTION.json` and the metadata Parquet, then generates the SQL for the requested view.

For a ZIP, the core reads the byte-zero index and fetches `COLLECTION.json` and every indexed Parquet range in one batch.

For a remote FOLDER or TACOCAT, the core fetches the Parquet files named by `taco:metadata`. It does not rely on directory listing. The files are stored in a local cache entry named `<id>-<container>-<origin>-<hash>` and laid out like a TACO FOLDER without `DATA/`.

Before reusing cached metadata, a reader MUST revalidate the remote source. It MAY use an ETag, modification time, content length, or an equivalent origin-provided value. It MUST refresh the entry when the source changed or cannot be validated. `TACO_CACHE_REFRESH` forces a refresh. A local archive is checked using its size and modification time. An open `Dataset` remains a snapshot of the metadata it loaded.

The cache keeps at most `TACO_CACHE_SIZE` bytes. The default is 10 GiB. When the limit is exceeded, the least recently opened entries are removed first. `TACO_CACHE_DIR` overrides the user cache directory. Local FOLDER and TACOCAT containers are read in place.

The reader reports remote download progress in interactive terminals and stays silent otherwise.

#### read(source, files)

`read` materializes one row per sample. It accepts only the source and an optional file selection. Row filtering and raw metadata access belong to the dataset SQL operation.

**source** is required. It accepts a local path or a remote location supported by the reader, including HTTP, S3, Google Cloud Storage, Azure, Hugging Face, and Source Cooperative. `read(source, files)` and `open_dataset(source).read(files)` MUST return the same result.

```
read("/data/cloudsen12.zip")
read("https://example.com/cloudsen12.zip")
read("s3://bucket/cloudsen12.zip")
read("hf://datasets/tacofoundation/cloudsen12/cloudsen12.zip")
read("source://taco/cloudsen12/cloudsen12.zip")
```

Remote file locations use the VSI prefix of their storage, such as `/vsicurl/`, `/vsis3/`, `/vsigs/`, `/vsiaz/`, `/vsihf/`, or `/vsisource/`.

**files** limits which structure declarations appear in the result. A fixed file is selected by its full contract path. A variable sequence is selected by its declaration, such as `img*[4,16].tif`. By default every declared file is returned.

The result includes the generated `taco:sample_index` and the stable logical `id`. TACOCAT also includes `source_file`, which identifies the ZIP containing each sample.

Every selected file has a `{file}::location` column calculated by the reader. Location columns are not stored in metadata. A Rumi file also has a `{file}::header` column when its metadata level declares `rumi:header`. These generated columns do not modify the dataset.

Collection metadata is not repeated in every result row. It is available through `Dataset.collection`.

The `dataset` relation and `read()` use the following column order. `source_file` is present only for TACOCAT or a source list.

| Result | Column order |
| --- | --- |
| `dataset` | `source_file` when present, `taco:sample_index`, `id`, sample metadata in `sample.parquet` schema order, then generated file columns |
| `read()` | The same columns as `dataset`, limited to the requested structure declarations when `files` is provided |

Generated file columns follow the selected declarations in structure order. Each `{file}::location` column is immediately followed by `{file}::header` when the file declares `rumi:header`. A variable sequence occupies one list column in the position of its declaration.

```
read("cloudsen12.zip")
# taco:sample_index | id        | ml:split | quality:cloud_cover | s2_l1c.tif::location | s2_l2a.tif::location | target.tif::location
# 0            | lima-001  | train    | 23.5                | /vsisubfile/...     | /vsisubfile/...     | /vsisubfile/...

# Two selected files
read("change_detection.zip", files=["before/B02.tif", "after/B02.tif"])
# taco:sample_index | id | ml:split | before/B02.tif::location | after/B02.tif::location

# Rumi assets carry their header
read("multisensor.zip")
# taco:sample_index | id       | optical.rumi::location | optical.rumi::header | radar.rumi::location | radar.rumi::header
# 0            | lima-001 | /vsisubfile/...       | b"LOVE..."          | /vsisubfile/...     | b"LOVE..."
```

A generated column is named after its structure path, followed by `::location` or `::header`, so `before/B02.tif` becomes `before/B02.tif::location`. The double `::` distinguishes generated columns from metadata fields, which contain exactly one `:`.

A variable sequence uses the path to its prefix. `before/img*[1,16].tif` becomes the `LIST(VARCHAR)` column `before/img::location`. A Rumi sequence also has a `LIST(BLOB)` column named `before/img::header`.

```
read("multitemporal_s2.zip")
# taco:sample_index | id       | ml:split | img::location
# 0            | lima-001 | train    | [/vsisubfile/..., /vsisubfile/..., ...]
# 1            | lima-002 | val      | [/vsisubfile/..., /vsisubfile/..., ...]
```

The list is ordered by the numeric sequence index and contains between the declared minimum and maximum number of locations.

`read()` MUST order rows by `taco:sample_index`. SQL results have no implicit order unless the query contains `ORDER BY`.

#### Dataset.sql(query)

Every reader MUST expose `Dataset.sql(query)`. It accepts one SQL query and exposes the following relations.

- `dataset` has one row per sample. It contains the sample metadata and all generated file columns returned by `read()`.
- Each metadata level has one raw relation with the same name, so the levels `sample`, `children`, and `children/before` are the relations `sample`, `children`, and `"children/before"`. A name containing `/` is written with double quotes in SQL.

Raw level relations keep their internal identity columns. Repeated field names remain unambiguous because SQL aliases identify the relation:

```sql
SELECT s."quality:score", c."quality:score"
FROM sample AS s
JOIN children AS c
  ON c."internal:parent_id" = s."internal:current_id"
```

Partial reads use SQL.

```
dataset = open_dataset("cloudsen12.zip")
rows = dataset.sql('SELECT * FROM dataset WHERE "taco:sample_index" < 100')
targets = dataset.sql('SELECT id, "target.tif::location" FROM dataset')
before = dataset.sql('SELECT * FROM "children/before"')
```

`read()` and `sql()` MUST execute through the same DuckDB context. `read()` returns `dataset` ordered by `taco:sample_index`. When `files` is provided, it returns an ordered projection containing only the selected generated file columns.

#### Inspection

Every reader MUST expose `inspect(source, query)`. It reads the contract without reading samples. `contract` returns structure and level entries as `kind` and `value` rows. `structure`, `levels`, `collection`, and `profile` return individual parts of that information.

`native_sql` exposes the low-level SQL generated by the shared core for debugging. It is separate from the public dataset SQL API.

### 8.3. Dataset API

`open_dataset()` returns a `Dataset` that holds the sources, collection, and shared contract. Every reader provides `read()` for the complete sample table and `sql()` for partial access.

```
dataset = open_dataset("cloudsen12.zip")

dataset.collection
dataset.contract
dataset.read()
dataset.read(files=["s2_l1c.tif", "target.tif"])
dataset.sql('SELECT * FROM dataset WHERE "taco:sample_index" = 10')
```

The same API accepts every TACO container and a list of compatible partitions.

```
parts = open_dataset(["cloudsen12_0.zip", "cloudsen12_1.zip"])
folder = open_dataset("cloudsen12/")
catalog = open_dataset("cloudsen12/.tacocat")
```

The reader verifies that every entry in a source list belongs to the same collection. It then streams their tables through `UNION ALL BY NAME` without materializing one table per source. `Dataset` merges the partition extents.

A TACOCAT is already consolidated and must be opened as one path.

Rows from a source list include `source_file`. The reader assigns `taco:sample_index` globally by following source-list order and then row order within each source. Reversing the source list may therefore change `taco:sample_index`, while `id` remains unchanged.


<figure class="dataset-figure">
<div class="dataset-map">
<div class="dm-head">
<div>
<div><span class="dm-class">taco.Dataset</span><span class="dm-title">CloudSEN12</span></div>
<div class="dm-description">Sentinel-2 image patches and cloud masks</div>
<div class="dm-facts"><span class="dm-fact">ZIP</span><span class="dm-fact">3 leaves</span><span class="dm-fact">2 levels</span><span class="dm-fact">8 fields</span></div>
</div>
<svg aria-label="TACO ZIP storage" class="dm-store" role="img" viewbox="0 0 160 125">
<defs><lineargradient id="spec-cylinder" x1="0" x2="1"><stop offset="0" stop-color="#FAEEDA"></stop><stop offset=".55" stop-color="#FAC775"></stop><stop offset="1" stop-color="#EF9F27"></stop></lineargradient></defs>
<path d="M25 28v66c0 11 25 20 55 20s55-9 55-20V28" fill="url(#spec-cylinder)" stroke="#854F0B" stroke-width="1.4"></path>
<ellipse cx="80" cy="28" fill="#FAEEDA" rx="55" ry="19" stroke="#854F0B" stroke-width="1.4"></ellipse>
<path d="M25 61c0 11 25 20 55 20s55-9 55-20M25 85c0 11 25 20 55 20s55-9 55-20" fill="none" opacity=".45" stroke="#854F0B" stroke-width=".7"></path>
<text fill="#633806" font-size="12" font-weight="700" text-anchor="middle" x="80" y="67">ZIP</text>
</svg>
</div>
<div class="dm-section">
<div class="dm-summary"><span class="dm-arrow">v</span><span class="dm-name">Structure</span><span class="dm-count">3 leaves</span></div>
<div class="dm-content dm-graph">
<svg aria-label="Sample structure" height="170" role="img" viewbox="0 0 360 170" width="360">
<path class="dm-edge" d="M 180 56 V 85 H 52 V 114"></path><path class="dm-edge" d="M 180 56 V 85 H 180 V 114"></path><path class="dm-edge" d="M 180 56 V 85 H 308 V 114"></path>
<g class="dm-node dm-root"><rect height="40" rx="7" width="84" x="138" y="16"></rect><text class="dm-label" text-anchor="middle" x="180" y="33">sample</text><text class="dm-kind" text-anchor="middle" x="180" y="47">root</text></g>
<g class="dm-node dm-file"><rect height="40" rx="7" width="88" x="8" y="114"></rect><text class="dm-label" text-anchor="middle" x="52" y="131">s2_l1c.tif</text><text class="dm-kind" text-anchor="middle" x="52" y="145">file</text></g>
<g class="dm-node dm-file"><rect height="40" rx="7" width="88" x="136" y="114"></rect><text class="dm-label" text-anchor="middle" x="180" y="131">s2_l2a.tif</text><text class="dm-kind" text-anchor="middle" x="180" y="145">file</text></g>
<g class="dm-node dm-file"><rect height="40" rx="7" width="88" x="264" y="114"></rect><text class="dm-label" text-anchor="middle" x="308" y="131">target.tif</text><text class="dm-kind" text-anchor="middle" x="308" y="145">file</text></g>
</svg>
</div>
</div>
<div class="dm-section dm-folded"><div class="dm-summary"><span class="dm-arrow">&gt;</span><span class="dm-name">Metadata</span><span class="dm-count">8 fields</span></div></div>
<div class="dm-section dm-folded"><div class="dm-summary"><span class="dm-arrow">&gt;</span><span class="dm-name">Collection</span><span class="dm-count">cloudsen12</span></div></div>
<div class="dm-section dm-folded"><div class="dm-summary"><span class="dm-arrow">&gt;</span><span class="dm-name">Sources</span><span class="dm-count">1 source</span></div></div>
</div>
<figcaption><b>Figure 4.</b> Notebook representation of a Dataset. The contract is visible without reading the sample table.</figcaption>
</figure>


`Dataset` is not a dataframe. It holds dataset semantics, while `read()` and `sql()` return tables. `inspect()` provides direct access to the contract and low-level native SQL.

Notebook environments use the Dataset HTML representation to show the collection identity, structure, and metadata levels without materializing the sample table.

## Annex A: Migration from v2

TACO v3 is not compatible with v2 datasets. Existing datasets MUST be rebuilt to work with v3 tools. This annex summarizes the differences.

### A.1. Summary of Changes

| Aspect | v2 | v3 |
| --- | --- | --- |
| Contract definition | Inferred from first sample at runtime (PIT) | Declared explicitly before any data is written |
| Core abstractions | SAMPLE, TORTILLA, TACO | Contract, Collection, Sample, Folder, Asset |
| Sample identity | String IDs (`id` field) | `id` for logical identity; local integer indices for row position |
| Sample types | FILE or FOLDER discriminator | No type field. The contract defines the structure. |
| Irregular structures | Padding with `__TACOPAD__` placeholders | Variable sequences (`prefix*[a,b].ext`) |
| Metadata storage | Dual system (consolidated `levelX.parquet` + local `__meta__` per folder) | Consolidated only (one Parquet per contract level, no local metadata) |
| Parquet naming | By depth (`level0.parquet`, `level1.parquet`, ...) | By row level (`sample.parquet`, `children.parquet`, `children__before.parquet`, ...) |
| Extension system | Formal `extend_with()` interface with SampleExtension, TortillaExtension, TacoExtension classes | Scoped Pydantic groups for sample, folder, asset, and collection metadata |
| Structural constraint | Position-Invariant Tree inferred at runtime | Structure and metadata declared in a contract |
| Library dependency | GDAL required | VSI path convention. GDAL is one implementation. |
| Reader implementation | TacoReader with separate container backends | One C++ core that generates SQL for every binding |
| Writer implementation | TacoToolbox (Sample, Tortilla, Taco classes) | `taco.open_writer()` |
| ZIP extension | `.tacozip` | `.zip`, with the CoZIP profile byte as the only type signal |
| TACOCAT format | Binary file with 128-byte header, fixed 7-entry index table | Directory with merged Parquets and COLLECTION.json |
| TACOLLECTION | Separate `TACOLLECTION.json` file | Merged into TACOCAT COLLECTION.json via `taco:sources` field |
| Spec versioning | SemVer (`2.0.0`) | SemVer (`3.0.0`) |
| Hierarchical navigation | `read()` method traversing `__meta__` files | SQL relations for each metadata level |
| Filtering | `filter_bbox()`, `filter_datetime()` with cascading JOINs | SQL `WHERE` clauses on Parquet columns |
| Concatenation | `concat()` with column modes | SQL `UNION` or TACOCAT consolidation |

### A.2. Contract vs PIT

The main change in v3 is that the dataset structure is explicit.

In v2, the writer inferred the structure from the first sample. PIT then required later samples to have the same children, in the same positions, with the same types. Missing positions were filled with `__TACOPAD__` placeholders.

In v3, the contract declares the file layout and metadata schema before writing begins. Every sample is checked against it. Nothing is inferred and no padding is needed.

The contract also determines the Parquet files in advance. Their names describe the rows they contain, such as `children__before.parquet`, instead of using a depth such as `level1.parquet`. Variable sequences replace padding when the number of files differs between samples.

### A.3. Metadata System

v2 stored metadata twice. Consolidated `levelX.parquet` files supported queries across the dataset, while local `__meta__` files supported navigation inside each folder.

v3 stores sample, folder, and asset metadata in the Parquet files under `METADATA/`. Collection metadata is stored once in `COLLECTION.json`. Relationships between tabular levels use `internal:parent_id` and SQL joins. Local `__meta__` files are no longer needed because the contract already describes every level.

### A.4. Reader Architecture

v2 used a Python reader with separate backends for ZIP, FOLDER, and TACOCAT. DuckDB was hidden behind TACO-specific dataset and dataframe classes.

v3 moves reading into one shared C++ core. The core detects the container, reads its metadata, builds the file locations, and generates the SQL for each query.

### A.5. Extension System

v2 extensions combined schema definitions with metadata computation through base classes and `extend_with()`.

v3 uses namespaced Pydantic models for passive metadata and a generic `Extension` abstraction for writer-time operations. The namespace determines the stored field prefix. Extensions combine optional validated inputs, declared outputs, local asset access, and explicit dependencies.

`taco` includes Spatial and STAC extensions that compute each footprint and bounding box from a grid, a Rumi extension for canonical headers and per-band statistics, and composable operations such as MajorTOM. The writer resolves their dependency graph during `run()`. Datasets can define additional models or extension subclasses without modifying the writer core.

### A.6. TACOCAT and TACOLLECTION

v2 stored TACOCAT in a binary file with a fixed header. It used a separate TACOLLECTION JSON document for dataset metadata.

v3 stores TACOCAT as a `.tacocat/` directory containing merged Parquet files and one `COLLECTION.json`. The `taco:sources` field replaces TACOLLECTION.

### A.7. Migration Path

There is no automatic migration tool. The two versions differ in structure discovery, sample identity, metadata layout, and Parquet naming.

To migrate, producers MUST define a v3 contract and rebuild the dataset with `taco`. The GeoTIFF, NetCDF, and other data files can remain unchanged. Only their packaging and metadata must be rewritten.

## Annex B: History

TACO began in Valencia, Spain, at the Image and Signal Processing group of the Universitat de València. Early discussions brought together Julio Contreras, Oscar Pellicer, Simon Donike, Chen Ma from HIT, David Montero from the University of Leipzig, and Cesar Aybar.

During that period, after spending more than a month harmonizing cloud detection datasets, we sketched a general solution. We wanted something simple for our own work and the datasets produced at ISP. That idea became TACO v1, which combined a custom binary layout with Parquet metadata and built its tooling around GDAL and dataframes.

The first format established the core idea but exposed several design problems. Conversations at ESA Living Planet 2025 shaped TACO v2. Jérémy Anger from Kayrros and Université Paris-Saclay suggested replacing the custom binary format with ZIP.

Discussions with Mikolaj Czerkawski at Asterisk about MajorTOM influenced lazy loading. Nils Lehmann and Adam Stewart from the University of Munich and TorchGeo contributed their experience with machine learning datasets to the API design.

OceanTACO, built with Nils Lehmann at TUM, tested v2 on ocean remote sensing data and exposed limitations that later shaped the v3 contract. Nate Mankovich at ISP helped define the Position-Invariant Tree that underpinned v2. Luis Gómez-Chova and Gustau Camps-Valls at ISP have helped question and refine TACO from the beginning.

TACO v2 was used to publish small and medium collections. Production datasets built at Asterisk Labs exposed limits in its structure inference, metadata duplication, and reader architecture.

TACO v3 replaces PIT and padding with an explicit contract. One reader core replaces three Python backends. Local `__meta__` files and the binary TACOCAT header are no longer used.
