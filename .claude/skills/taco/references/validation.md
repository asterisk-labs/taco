# Validation

Sources: `python/taco/validate.py`, `python/taco/container/view.py`, SPEC 7.6.

```python
report = taco.validate(path, check_data=True)
report.ok          # no errors; warnings do not affect it
report.errors      # list[Issue]
report.warnings
report.issues      # both, in the order they were found
report.container   # "zip", "folder", "tacocat", or None when opening failed
report.raise_for_errors()   # ValidationFailed with every error
print(report)      # "<path> (zip): valid" or a header plus one line per issue
```

`Issue` is `(severity, code, message)` and prints as `[error] schema: children:
missing columns [...]`. `validate` is **local only**: it reads the container with
pyarrow and `zipfile`, not through the native core. `check_data=False` skips the
payload checks, the CRC-32 of each ZIP payload included, which is the expensive part
on a large archive.

Validation collects problems across the dataset. Missing internal columns, null
relative paths, and CRSs that pyproj cannot transform become report issues.

`parse_collection_json` enforces the core's JSON rules: UTF-8, no NaN or
Infinity, no repeated key in an object, no number beyond the finite double range
(`1e400`), and at most 256 nesting levels. Field types must match SPEC 5.5;
`taco:structure` must be a list of strings. Invalid collection data produces a
`container` issue because the dataset cannot be opened.

Row checks go beyond the schema: child rows must be grouped by parent in parent
order and follow the structure order (`parent_id`, `structure`), a field that
declares `files` must be null on other files' rows and on folder rows (`schema`),
and MajorTOM codes are recomputed from the stored centroids and parameters
(`extensions`). JSON Schema violations are reported up to 10 per extension, then
"breaks <id> in N more places".

## What runs

`validate` opens a `DatasetView`, then checks the collection, extensions, metadata
files, sample ids, each level, and finally the container.

`detect_container` decides by shape: a file is `zip`; a directory with
`COLLECTION.json` and `METADATA/` is `folder`; a directory with `COLLECTION.json` and
`*.parquet` is `tacocat`. Anything else fails to open and the report carries a single
`container` error.

Opening reads **every** level eagerly, so a **missing** Parquet never reaches the
per-level checks: it fails the open and the report is one `container` error with
`report.container = None`, printing `(unknown)`.

```
bad (unknown): 1 error(s), 0 warning(s)
  [error] container: could not read level 'children' from <dir>: <dir>/METADATA/children.parquet
```

An **extra** file in `METADATA/`, even a `.DS_Store`, does reach the metadata check:

```
bad (folder): 1 error(s), 0 warning(s)
  [error] metadata: unexpected files ['children__ghost.parquet']
```

A TACOCAT is validated with its partitions. Each partition listed in `taco:sources`
must exist and is checked like a standalone ZIP, its issues prefixed with
`partition '<file>': `. The catalog must then repeat the partition rows in
`taco:sources` order with shifted ids, copy each partition extent into
`taco:sources`, keep the same collection metadata apart from `extent`, `taco:sources`
and the summaries, and hold summaries that combine the partition values the way
`consolidate` does.

## Issue codes

| Code | Checks |
| --- | --- |
| `container` | The dataset could not be opened or parsed at all |
| `id` | `id` present, non-null, non-empty, unique |
| `sources` | `taco:sources` present only in TACOCAT, its partitions well formed, present, and matching the catalog rows, collection metadata and summaries |
| `tasks` | Unrecognized task strings (**warning**) |
| `metadata` | Exactly one Parquet per contract level, no extra files |
| `schema` | `taco:level` metadata, exact column set, types, nullability, descriptions, no nulls in a non-nullable column |
| `current_id` | `internal:current_id` equals the row position |
| `parent_id` | Every `internal:parent_id` resolves to a parent row, and child rows are grouped by parent in parent order |
| `relative_path` | Sample rows use `<idx>`; child rows use `<idx>/<folder>/<name>` and point at their own parent |
| `structure` | Each parent's children match the contract: fixed files present, sequences contiguous and within bounds, nothing extra, in structure order |
| `offsets` | File rows carry `internal:offset`/`internal:size`, folder rows do not, sizes are non-zero and match the archive |
| `cozip` | The CoZIP index is readable and lists exactly `COLLECTION.json` and the level Parquet files, at the offset and size of each payload |
| `zip` | Empty comment, no duplicate entries, `__cozip__` first, priority block last and contiguous, STORE mode, no directory or empty entries, CoZIP entry names, no encryption, data descriptor or masked header flags, local headers that agree with the central directory, no overlapping entries, the End of Central Directory record last on a single disk, and payloads that match their CRC-32 (`check_data`) |
| `data` | Every declared file exists, is non-empty, and nothing under `DATA/` is undeclared |
| `source_file` | TACOCAT levels carry `internal:source_file` and only reference listed partitions |
| `profile` | Every STAC, spatial or temporal row follows its profile: a valid grid or geometry, a CRS that reaches EPSG:4326, consistent datetimes |
| `extensions` | `taco:extensions` present; ZIP and FOLDER `extent` matches the writer's row summary within 1e-9 degrees and is absent without spatial rows; built-in namespaces listed once; `COLLECTION.json` valid against each known schema; MajorTOM columns and ranges valid; extension fields used only at their allowed scope. An unknown identifier is a **warning** |

Representative messages:

```
[error] id: sample: id is empty on 1 rows
[error] id: sample: id must be unique (1 duplicates)
[error] metadata: unexpected files ['children__extra.parquet']
[error] schema: children: Parquet schema must declare taco:level='children'
[error] schema: sample: missing columns ['split:split']
[error] schema: sample: column 'quality:score' nullability does not match the contract
[error] schema: sample: column 'split:split' contains null values
[error] current_id: sample: internal:current_id must equal the row position
[error] parent_id: children: 3 rows reference a missing parent (first row 12)
[error] relative_path: children: internal:relative_path entries do not follow '<sample>/<name>'
[error] structure: children: 3 parent(s) have children that do not match the contract
[error] offsets: children: file row '0/mask.tif' has no internal:offset
[error] offsets: children: folder row '0/before' must not carry internal:offset
[error] zip: priority files must form the final contiguous entry block
[error] zip: entries must use STORE: 'DATA/0/mask.tif'
[error] zip: partition 'b.zip': payloads do not match their CRC-32: 'DATA/0/mask.tif'
[error] cozip: the CoZIP index gives 'METADATA/sample.parquet' offset <n> and size <n>, but its payload has offset <n> and size <n>
[error] data: 1 data files referenced by metadata are missing (first: 0/data.bin)
[error] data: 1 data files are empty
[error] data: 1 files under DATA/ are not described by metadata (first: 1/stray.txt)
[error] sources: taco:sources.samples does not match its partitions
[error] sources: partition 'europe.zip' cannot be found
[error] sources: the collection metadata of 'b.zip' differs from the catalog in ['id']
[warning] tasks: unrecognized task types ['segmentaton']
```

Paths in `data` messages are relative to `DATA/`, without that prefix. The
`relative_path` pattern names the level's folder chain, so `children/before` reports
`'<sample>/before/<name>'`.

## Type checks are exact

A contract declaring `{"type": "int64", "nullable": false}` is not satisfied by a
physical `int32` column even when every value fits, nor by a nullable `int64` column.
Descriptions are compared too: a non-empty description must be stored in the Arrow
field metadata under `description`. Nested nullability counts: `list<int64>` and
`list<int64?>` are different columns.

## Reader-side rejection

A reader does not run `validate`. The core rejects what it must while opening, and
these are the failures you see instead of a report:

```
unsupported TACO version '2.0.0' in <source>; expected 3.0.0
COLLECTION.json has no taco:structure key: <source>
COLLECTION.json: taco:structure must be a non-empty array: <source>
COLLECTION.json levels 'children/a_/b' and 'children/a/_b' would share the Parquet file children__a___b.parquet: <source>
TACO dataset has no children.parquet for metadata level 'children': <source>
Parquet file children__ghost.parquet matches no metadata level in COLLECTION.json: <source>
COLLECTION.json structure requires sample and children metadata levels: <source>
TACO dataset has no sample.parquet: <source>
TACO dataset has child levels but no children.parquet: <source>
METADATA level 'children/x' has no parent level 'children': <source>
METADATA level 'other' is not 'children' or below it: <source>
taco needs a TACO-profile archive (profile=2). Got profile=<n>
COLLECTION.json is larger than 64 MiB, refusing to read it: <source>
COLLECTION.json: invalid Rumi field 'rumi:stats': <source>
COLLECTION.json: field 'rumi:mean' must have type double: <source>
```

The reader checks the contract and the level graph; it does not read every payload
before answering an unrelated query. Use `validate` before publishing.

## Practical use

```python
report = taco.validate("ds.zip")
if not report.ok:
    print(report)
    report.raise_for_errors()
```

Every example under `python/examples/` ends with `assert taco.validate(...).ok`, and
the writer test suite validates each case it builds; keep that habit when adding a
writer feature. Use `check_data=False` for a fast schema and metadata pass over a
large archive, then a full run before release.
