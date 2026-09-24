# Writing: writer lifecycle, containers, export and consolidation

Sources: `python/taco/writer/`, `python/taco/container/`, SPEC 8.1.

`taco.open_writer()` is the only conforming writer. Another implementation may read,
validate or inspect a TACO dataset, but may not claim to write one.

## Selecting a writer

```python
taco.open_writer(
    collection, output,
    append=False,            # FOLDER only; continues an existing dataset
    overwrite=False,         # replace an existing output
    link=False,              # FOLDER only; hardlink assets instead of copying
    row_group_size=65_536,
    batch_size=10_000,       # rows buffered per level before a flush
    parquet_options=None,    # merged over {"compression": "zstd", "write_statistics": True}
    partition_size=None,     # ZIP only; int bytes or "4GB"
    partition_by=None,       # ZIP only; one sample-level field
    progress=False,
    workers=1,               # partitioned ZIP only
)
```

A `.zip` suffix selects `ArchiveWriter`; any other path selects `FolderWriter`. The
guards are explicit: `ZIP datasets are immutable` for `append`, `link is only valid
for FOLDER datasets`, `partitioning is only valid for ZIP datasets`, `workers is only
valid for partitioned ZIP datasets` (or `workers requires a partitioned ZIP dataset`
when the output is a `.zip` without partitioning), `a FOLDER dataset is a directory,
not an archive name`, and `taco:sources is reserved for TACOCAT` when the collection carries it.

`row_group_size` belongs to the writer, not `parquet_options`: passing it there raises
`pass row_group_size as a writer argument, not in parquet_options`.

## Lifecycle

```python
with taco.open_writer(collection, "ds.zip") as writer:
    writer.add(sample)                 # validates and stages; returns the local index
    writer.extend(samples)             # add() in a loop; returns the new count
    result = writer.run()              # computes derived metadata, writes, publishes
```

- `add()` runs `Contract.prepare_sample`, materializes inline bytes into the staging
  directory, checks the size of every asset and appends a pickled record to a
  disk-backed stream. Sample ids go into a SQLite uniqueness index. Nothing about the
  output is touched.
- `run()` closes the input and builds. It is the commit point, and is **idempotent**:
  a second call returns the same `BuildResult`. Afterwards the writer is `succeeded`
  and `add()` raises `add() is only valid while the writer is open; state=succeeded`.
- **Leaving the context manager does not call `run()`**. `close()` only removes the
  temporary directory; a writer that is never run publishes nothing.
- A writer with no samples raises `cannot build a dataset without samples`.

`BuildResult` carries `path`, `samples`, `data_files`, `metadata_files`, `size`, and
`parts` for a partitioned build.

## How a build becomes atomic

A ZIP is planned, then written to a `mkstemp` temporary beside the target and renamed
into place only after `cozip_write` returns, so a reader never sees half an archive.
The published file follows the umask rather than the `0600` of `mkstemp`.

A new FOLDER is built in a sibling `.<name>.build-*` directory and moved in one step.
An append writes new `DATA/<idx>/` directories first, then replaces `METADATA/` and
`COLLECTION.json` together through `publish_many`, which keeps a backup and rolls both
back if either fails. New sample directories are removed on failure, so a failed
append leaves the published dataset untouched.

Parquet is rewritten, never edited: an append calls `write_existing` for every level
before the first new row, because ids already assigned cannot be renumbered.

## Append

```python
with taco.open_writer(collection, "ds", append=True) as writer:
    writer.add(sample)
    writer.run()
```

An append re-opens the dataset and refuses anything that would change meaning:

| Message | Cause |
| --- | --- |
| `append=True needs an existing FOLDER dataset at <path>` | Nothing there, or not a TACO folder |
| `append cannot change the dataset id` | The collection `id` differs |
| `the existing dataset was built with a different contract` | Serialized contracts differ |
| `append uses different extension metadata: ['majortom:dist_km']` | A semantic extension parameter changed |
| `append would duplicate sample ids: ['s0']` | Existing ids clash with the new ones |

New samples continue the numeric indices already present. `append` and `overwrite` are
mutually exclusive, and `overwrite=True` on a non-empty directory that is not a TACO
folder is refused: `refusing to overwrite <path>: it is not a TACO folder dataset`.

## Partitioned ZIP and TACOCAT

`partition_size` fills partitions by payload bytes in sample order, naming them
`part0001`, `part0002`, ... `partition_by` groups on one **sample-level** field, using
the sanitized value as the label. The field must exist in the sample level and may not
be `id`:

```
partition_by cannot be 'id'; it is unique, so every sample would be its own partition
partition_by field 'ml:splt' is not sample metadata; available: ['stac:crs', ..., 'ml:split']
partition values 'a/b' and 'a_b' collide on file name 'a_b'
use either partition_size or partition_by, not both
```

Partitioning on a field an extension produces works: the writer runs the sample-level
extensions over batches first so the value exists before grouping.

The output is `<stem>_<label>.zip` beside a `.tacocat/` directory, and all of them are
published together. `BuildResult.path` is the catalog, `BuildResult.parts` the
archives. If only one partition results, the writer falls back to a single archive.
`workers > 1` builds partitions on a thread pool and shows one combined progress bar.

```python
with taco.open_writer(collection, "ds.zip", partition_by="ml:split", overwrite=True) as writer:
    writer.extend(samples)
    catalog = writer.run().path          # .../.tacocat
taco.open_dataset(catalog)               # or its parent directory
```

## consolidate

```python
taco.consolidate(archives, output=None, *, name=".tacocat", overwrite=False,
                 row_group_size=65_536, parquet_options=None) -> Path
```

Merges the metadata of several ZIP partitions into one catalog that references them.
`output` defaults to the shared parent of the archives; `partitions live in different
directories; pass output= explicitly` when there is no single parent. Each source is
re-checked:

```
TACOCAT consolidates archive partitions, got folder: <path>
<b.zip> was built with a different contract than <a.zip>
<b.zip> has different collection metadata than <a.zip>
level 'children' in <b.zip> has a different schema than <a.zip>
partitions contain duplicate sample ids: ['s0', 's1']
```

Consolidation reassigns `internal:current_id` from zero across the combined table,
rewrites `internal:parent_id` to match, adds `internal:source_file`, unions the
extents and writes `taco:sources`. `id` and `internal:relative_path` are untouched,
so a row still points into its own archive.

Unlike a source list, consolidation **does** enforce `id` uniqueness across
partitions.

## export

```python
taco.export(source, output, *, sql, overwrite=False, **collection_fields) -> BuildResult
```

Writes the complete samples selected by a query, through the normal writer.

- `sql` is required and may query `dataset`, `sample` or any level relation. The
  result must retain `taco:sample_index`: `sql must return taco:sample_index; use
  SELECT * or include it explicitly`.
- Selection is by sample. A row matching at `children__after` exports that sample's
  whole structure. Duplicates select once; indices outside the source are ignored.
- The contract, `id`, licenses, providers, tasks and collection metadata carry over.
  Keyword arguments replace collection fields; `contract` and `sources` may not be
  replaced. `extent` is recomputed and `taco:sources` dropped.
- Selected samples keep their `id` and are renumbered from zero in source order.
- Payloads are fetched in bounded batches (256 files or 256 MiB) through
  `taco_fetch`, so exporting ten samples from a remote dataset downloads ten samples.
  A local FOLDER source is read in place.

```python
taco.export("change-detection.zip", "clear.zip",
            sql='SELECT * FROM children__after WHERE "quality:cloud_cover" < 10')

taco.export("https://data.source.coop/major-tom/core-dem/", "sample.zip",
            sql="SELECT * FROM sample ORDER BY id LIMIT 10")
```

`export` reads one dataset: a source list raises `export reads one dataset`. Pass an
open `Dataset` to reuse its native handle.

## Progress

`progress=True` shows tqdm bars for the planning, metadata and packing phases, and
`leave=False` keeps them out of logs. Bars use `disable=None`, so they stay silent
when stderr is not a terminal. `export` always enables its own bar. Remote reads report
download progress through the core's callback.
