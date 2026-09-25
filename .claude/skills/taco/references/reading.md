# Reading: datasets, SQL, sources and the cache

Sources: `python/taco/reader/`, `core/src/sql.cpp`, `core/src/dataset.cpp`,
SPEC 8.2 and 8.3.

```python
dataset = taco.open_dataset(source)       # one path, or a list of compatible parts
dataset.collection                        # taco.Collection, with the merged extent
dataset.contract                          # taco.Contract
dataset.sources                           # the normalized source tuple
dataset.read(files=None)                  # pyarrow.Table, one row per sample
dataset.sql(query)                        # pyarrow.Table
taco.read(source, files=None)             # open_dataset(...).read(...)
```

`source` is a local path or a remote location: `http(s)://`, `s3://`, `gs://`,
`az://`, `hf://datasets/org/repo/file.zip`, `source://account/product/file.zip`. Paths
are expanded and resolved; a value containing `://` is left as written. Duplicate
paths in a list are refused.

A `Dataset` opens one native handle per source and reuses it for every query, so
repeated `sql()` calls do not reopen the container. It is a snapshot of the metadata
it loaded.

## What `read()` returns

Column order is fixed:

1. `source_file`, only for a TACOCAT or a source list;
2. `taco:sample_index` (`uint64`);
3. `id` (`string`);
4. sample metadata, in `sample.parquet` schema order;
5. one generated column per structure leaf, in structure order.

```
taco:sample_index | id | stac:geometry | ... | ml:split | before__B02.tif::location | extra::location
```

A generated name replaces `/` with `__` and appends `::location`. A leaf whose level
declares `rumi:header` is immediately followed by `{name}::header`. A variable
sequence uses its **prefix** and occupies one list column: `extra*[1,3].png` becomes
`extra::location` of type `list<string>`, ordered by the numeric index, with between
`min` and `max` entries.

The double `::` cannot collide with a metadata field, which contains exactly one `:`,
and the mapping is reversible because `__` and `:` are forbidden inside path
components.

`files=` restricts which leaves get a generated column; the metadata columns are
unchanged. A fixed file is named by its full contract path, a sequence by its whole
declaration:

```python
dataset.read(files="mask.tif")
dataset.read(files=["before/B02.tif", "extra*[1,3].png"])
```

An unknown value raises `ContainerError: taco: files contains unknown structure leaf:
nope.tif`. `read()` always orders by `taco:sample_index`.

Locations are computed, never stored. A ZIP or TACOCAT row becomes
`/vsisubfile/{offset}_{size},{archive}`; a FOLDER row becomes
`{root}/DATA/{idx}/{path}`. Any stored `taco:location` or `cozip:location` column is
stripped by the core before projection.

## Dataset.sql

```python
dataset.sql('SELECT * FROM dataset WHERE "ml:split" = \'train\'')
dataset.sql('SELECT id, "mask.tif::location" FROM dataset ORDER BY id')
dataset.sql('SELECT * FROM children__before')
```

Relations available to a query:

- `dataset`: one row per sample, exactly what `read()` returns.
- one per contract level, with `/` written as `__`: `sample`, `children`,
  `children__before`. These are **raw**: they keep `internal:current_id`,
  `internal:parent_id`, `internal:relative_path`, and `internal:offset`/`internal:size`
  for a ZIP or TACOCAT, and they carry no location column.

Joins between levels use the internal ids, and SQL aliases disambiguate a field name
declared at two levels:

```sql
SELECT s."quality:score", c."quality:score"
FROM sample AS s
JOIN children AS c ON c."internal:parent_id" = s."internal:current_id"
```

Three things to know:

- **Quote every user field.** Each contains a `:`, so `WHERE ml:split = 'train'` is a
  `ParserException`.
- **Errors come from DuckDB.** A wrong relation name raises `CatalogException: Table
  with name children__missing does not exist!`, not a `TacoError`. Only container,
  contract and sample problems raise `TacoError` subclasses.
- **SQL results have no implicit order.** Add `ORDER BY` when order matters; only
  `read()` promises `taco:sample_index` order.

The query is wrapped as `WITH <relations> SELECT * FROM (<query>) AS taco_query`, so
it must be a single statement. A trailing `;` is stripped; an empty query or one
containing NUL is refused. The connection is one cached DuckDB handle per thread,
configured with `TimeZone=UTC` so timestamps compare and print identically whatever
the host timezone is.

## Source lists and TACOCAT

```python
parts = taco.open_dataset(["ds_0.zip", "ds_1.zip"])
folder = taco.open_dataset("ds/")
catalog = taco.open_dataset("ds/.tacocat")     # or "ds/", which finds .tacocat
```

Every entry in a list must belong to the same collection; the check compares the
serialized collections with `extent` and `taco:sources` removed, and the level lists:
`source does not belong to the same collection: <path>`. A TACOCAT cannot appear in a
list (`a source list cannot contain TACOCAT datasets`); it is already consolidated.
Extents are merged with `Extent.union`, which handles the antimeridian.

Rows gain `source_file`, labelled by base name when those are unique and by full
source otherwise. The core streams the parts with `UNION ALL BY NAME` and assigns
`taco:sample_index` globally with `dense_rank()` over source order then row order, so
**reversing the list renumbers every sample**. `id` does not change.

A source list does **not** check `id` uniqueness across different compatible sources,
so two partitions may return the same `id` under distinct indices. Repeating the same
source path is rejected earlier with `source paths must be unique`. Only
`consolidate` checks duplicate ids across partitions.

## Two level orders

```python
dataset.contract.levels                 # ('sample', 'children', 'children/before', 'children/after')
taco.inspect(path, "levels")            # ['sample', 'children', 'children/after', 'children/before']
```

The Python contract derives levels from the structure, shallowest first and then in
declaration order. The core lists the Parquet files it found, sorted. Both satisfy
"parents before children", and both name the same set. Look levels up by name; never
pair the two lists positionally.

## inspect

`taco.inspect(path, query)` reads the contract without reading samples. `query` is one
of `collection`, `contract`, `levels`, `native_sql`, `profile`, `structure`; anything
else raises `query must be one of: collection, contract, levels, native_sql, profile,
structure`.

| Query | Returns |
| --- | --- |
| `structure` | `list[str]` of declarations |
| `levels` | `list[str]`, the core's order |
| `contract` | Arrow table of `kind`/`value` rows: `structure`, `level`, `derived` |
| `collection` | parsed `COLLECTION.json` as a dict |
| `profile` | the CoZIP profile: `none`, `flat`, `taco`, or `unknown:<n>` |

`profile` is archive-only: on a FOLDER or TACOCAT it raises
`ContainerError: could not open <path>: <path>: Is a directory`.

`taco.reader.inspect.native_sql(path, idx=..., level=..., layout="wide"|"long",
files=..., location=...)` returns the SQL the core generates, for debugging. `layout`
must be `wide` or `long`. It is a separate debugging surface from `Dataset.sql`; do
not build application queries on it.

The core's own options also expose a **long** layout: one row per *file*, folder rows
excluded, with `taco:sample_index`, `id`, `path`, `taco:location`, then every user
field of the dataset **sorted alphabetically**, carrying the value of the level the
row belongs to. A field declared at two levels is resolved deepest first, so a file
value shadows its folder's, which shadows the sample's.

```python
from taco.reader.inspect import native_sql
sql = native_sql("ds.zip", layout="long", idx=0)
# taco:sample_index | id | path | taco:location | band:name | ml:split | stac:geometry | ...
# 0 | s0 | mask.tif      | /vsisubfile/... | NULL | train | EPSG:4326
# 0 | s0 | before/B02.tif| /vsisubfile/... | B02  | train | EPSG:4326
```

The Python `Dataset` exposes only the wide view and the raw levels; the JavaScript
reader exposes both. Reach for long through `native_sql` for debugging, not in
application code.

## Remote sources and the metadata cache

Opening a remote dataset copies only the metadata. For a ZIP the core reads the
byte-zero CoZIP index and fetches `COLLECTION.json` and every indexed Parquet range in
one batch. For a remote FOLDER or TACOCAT it fetches the files named by
`taco:metadata`; it never lists a directory.

Cache entries live under `<root>/<id>-<container>-<origin>-<hash>` and are laid out
like a TACO FOLDER without `DATA/`, plus a `taco-cache.json` stamp.

| Variable | Effect |
| --- | --- |
| `TACO_CACHE_DIR` | Cache root. Default `$XDG_CACHE_HOME/taco`, else `~/.cache/taco`, else `%LOCALAPPDATA%\taco\cache`, else `.taco-cache` |
| `TACO_CACHE_SIZE` | Cap in bytes, default 10 GiB. Least recently opened entries are evicted first |
| `TACO_CACHE_REFRESH` | Any non-empty value rebuilds the entries it touches |

A remote entry is revalidated against the origin-reported object size; a local archive
against its size and modification time. If the probe fails, opening fails rather than
serving metadata that cannot be validated. Local FOLDER and TACOCAT containers are
read in place with no cache entry.

Payload bytes are never cached. A `::location` path is fetched by whatever reads it,
and `taco.export` downloads payloads through `taco_fetch` in bounded batches.

Downloads report progress through the core callback, which the Python binding renders
with tqdm in interactive terminals and suppresses otherwise.

## Notebooks

`Dataset._repr_html_` renders the collection identity, a structure graph, the metadata
levels and the sources without materializing the sample table. `repr()` is
`Dataset('<id>', source=...)` or `sources=<n>`.
