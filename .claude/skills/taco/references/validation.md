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
payload checks, which is the expensive part on a large archive.

Validation collects every problem rather than stopping at the first, so a broken
dataset produces a complete report.

## What runs

`validate` opens a `DatasetView`, then runs, in order: the collection check, the
metadata file check, the sample id check, the per-level checks, and one
container-specific check.

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

An **extra** Parquet does reach the metadata check:

```
bad (folder): 1 error(s), 0 warning(s)
  [error] metadata: unexpected Parquet files ['children__ghost.parquet']
```

## Issue codes

| Code | Checks |
| --- | --- |
| `container` | The dataset could not be opened or parsed at all |
| `id` | `id` present, non-null, non-empty, unique |
| `sources` | `taco:sources` present only in TACOCAT, its partitions well formed and consistent |
| `tasks` | Unrecognized task strings (**warning**) |
| `metadata` | Exactly one Parquet per contract level, no extra files |
| `schema` | `taco:level` metadata, exact column set, types, nullability, descriptions, no nulls in a non-nullable column |
| `current_id` | `internal:current_id` equals the row position |
| `parent_id` | Every `internal:parent_id` resolves to a parent row |
| `relative_path` | Sample rows use `<idx>`; child rows use `<idx>/<folder>/<name>` and point at their own parent |
| `structure` | Each parent's children match the contract: fixed files present, sequences contiguous and within bounds, nothing extra |
| `offsets` | File rows carry `internal:offset`/`internal:size`, folder rows do not, sizes are non-zero and match the archive |
| `cozip` | The CoZIP index matches `taco:metadata` and is readable |
| `zip` | Empty comment, no duplicate entries, `__cozip__` first, priority block last and contiguous, STORE mode, no directory entries |
| `data` | Every declared file exists, is non-empty, and nothing under `DATA/` is undeclared |
| `source_file` | TACOCAT levels carry `internal:source_file` and only reference listed partitions |

Representative messages:

```
[error] id: sample: id is empty on 1 rows
[error] id: sample: id must be unique (1 duplicates)
[error] metadata: unexpected Parquet files ['children__extra.parquet']
[error] schema: children: Parquet schema must declare taco:level='children'
[error] schema: sample: missing columns ['ml:split']
[error] schema: sample: column 'quality:score' nullability does not match the contract
[error] schema: sample: column 'ml:split' contains null values
[error] current_id: sample: internal:current_id must equal the row position
[error] parent_id: children: 3 rows reference a missing parent (first row 12)
[error] relative_path: children: internal:relative_path entries do not follow '<sample>/<name>'
[error] structure: children: 3 parent(s) have children that do not match the contract
[error] offsets: children: file row '0/mask.tif' has no internal:offset
[error] offsets: children: folder row '0/before' must not carry internal:offset
[error] zip: priority files must form the final contiguous entry block
[error] zip: entry 'DATA/0/mask.tif' is not STORE
[error] data: 1 data files referenced by metadata are missing (first: 0/data.bin)
[error] data: 1 data files are empty
[error] data: 1 files under DATA/ are not described by metadata (first: 1/stray.txt)
[error] sources: taco:sources.samples does not match its partitions
[warning] tasks: unrecognized task types ['segmentaton']
[warning] sources: partition 'europe.zip' cannot be found
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
COLLECTION.json metadata levels do not match its Parquet files: <source>
COLLECTION.json structure requires sample and children metadata levels: <source>
TACO dataset has no sample.parquet: <source>
TACO dataset has child levels but no children.parquet: <source>
METADATA level 'children/x' has no parent level 'children': <source>
METADATA level 'other' is not 'children' or below it: <source>
taco needs a TACO-profile archive (profile=2). Got profile=<n>
COLLECTION.json is larger than 64 MiB, refusing to read it: <source>
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
