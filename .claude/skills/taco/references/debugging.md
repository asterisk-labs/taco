# Debugging and repository layout

## Which exception am I looking at

`taco.TacoError` is the base. Everything under it:

| Exception | Also a | Raised when |
| --- | --- | --- |
| `ContractError` | `ValueError` | The structure or metadata schema is invalid |
| `TypeSpecError` | `ContractError` | A type string cannot be parsed |
| `SampleError` | `ValueError` | A sample does not fit its contract |
| `CollectionError` | `ValueError` | `COLLECTION.json` or a `Collection` argument is invalid |
| `WriterError` | `RuntimeError` | The writer was used out of lifecycle, or publishing failed |
| `ContainerError` | `RuntimeError` | A container cannot be opened, or the native core failed |
| `ConsolidationError` | `RuntimeError` | Partitions cannot be merged |
| `ValidationFailed` | `RuntimeError` | `report.raise_for_errors()` on a report with errors |

Two families are **not** `TacoError`:

- **DuckDB** exceptions from `Dataset.sql` and `export`:

  ```
  CatalogException: Catalog Error: Table with name children/nope does not exist!
  BinderException: Binder Error: Referenced column "nope:x" not found in FROM clause!
  ParserException: Parser Error: syntax error at or near ":"
  ```

  The last one is almost always an unquoted field; quote everything containing `:`.
- **Pydantic** `ValidationError` from constructing a metadata model. The writer's own
  coercion errors come later as `SampleError: invalid <field> at '<level>': ...`.

Plain `ValueError` and `TypeError` also come from argument checks in `open_writer`,
`export`, `consolidate` and `inspect`.

## Error lookup

### Loading the native library

```
ContainerError: could not load the TACO core from <path>: <dlopen error>.
Build it with `make core` or set TACO_LIB.
```

Python looks at `$TACO_LIB` first, then `taco/_lib/libtaco.dylib` (macOS),
`libtaco.so` (Linux) or `taco.dll` (Windows). In a source checkout run `make core`,
which builds and copies the library there. A wheel already bundles it.

```
ContainerError: the TACO core at <path> has C API 1, expected 2
```

A stale `TACO_LIB` or a leftover `_lib/` from another version. Rebuild, or unset
`TACO_LIB`.

### Opening a dataset

| Message | Cause |
| --- | --- |
| `could not open <path>: ... No such file or directory` | Wrong path, or a relative path resolved from another cwd |
| `could not open <path>: ...: Is a directory` | An archive-only call on a directory; `inspect(..., "profile")` does this |
| `directory has no COLLECTION.json or .tacocat/COLLECTION.json: <dir>` | Not a TACO directory |
| `taco needs a TACO-profile archive (profile=2). Got profile=<n>` | A plain ZIP, or a flat CoZIP |
| `COLLECTION.json metadata levels do not match its Parquet files` | A level was added or a Parquet removed by hand |
| `source does not belong to the same collection: <path>` | A source list mixing collections |
| `a source list cannot contain TACOCAT datasets` | Open the catalog on its own |
| `COLLECTION.json is larger than 64 MiB, refusing to read it` | Collection metadata is being abused as a data store |

### Remote sources

Transport goes through karu: HTTP(S), S3, Google Cloud, Azure, Hugging Face and
Source Cooperative, as `s3://bucket/key` or `/vsis3/bucket/key`, and so on for each
backend. Credentials come from the environment karu documents in
`extern/karu/CONFIGURATION.md`; for a private Hugging Face repository run
`hf auth login` and karu reads that token.

Opening fails rather than serving stale metadata when the origin size cannot be
probed, so a 403 surfaces at `open_dataset`, not at the first read. Check the
credentials for the **object**, not the bucket root: the core probes
`COLLECTION.json` under the source.

When a remote dataset looks out of date, the cache is revalidated against the origin
size. A source that changed without changing size will not be noticed; force it:

```bash
TACO_CACHE_REFRESH=1 python script.py
```

Move or shrink the cache with `TACO_CACHE_DIR` and `TACO_CACHE_SIZE` (plain bytes,
default 10 GiB). Tests set `TACO_CACHE_DIR` per test through the `isolated_cache`
fixture; do the same when a test touches a remote source.

### Query surprises

| Symptom | Cause |
| --- | --- |
| `ParserException: syntax error at or near ":"` | An unquoted metadata field. Write `"ml:split"` |
| `ParserException: syntax error at or near "/"` | An unquoted level relation. Write `"children/before"` |
| `CatalogException: Table with name children__before does not exist!` | Level relations keep their `/`: `"children/before"` |
| A file column is missing | It was excluded by `files=`, or the leaf name is not the full declaration |
| A sequence column is a list of nulls | No sample matched the pattern; check for leading zeros |
| `taco:sample_index` changed between runs | A source list in a different order. Use `id` |
| Rows come back unordered | Only `read()` sorts. Add `ORDER BY` to SQL |
| Timestamps print in the wrong zone | The reader forces `TimeZone=UTC`; the values are UTC |

To see what the core generated:

```python
from taco.reader.inspect import native_sql
print(native_sql("ds.zip", layout="wide", idx=[0, 10]))
```

### Writing

| Message | Cause |
| --- | --- |
| `cannot build a dataset without samples` | `run()` with nothing added |
| `add() is only valid while the writer is open; state=succeeded` | `add()` after `run()`. Open a new writer |
| `output already exists (set overwrite=True): <path>` | The target exists |
| `directory is not empty (set overwrite=True): <path>` | A non-empty FOLDER target |
| `refusing to overwrite <path>: it is not a TACO folder dataset` | `overwrite=True` on a directory holding something else |
| `asset source is not a regular file: <path>` | A directory or missing file. This one is a plain `FileNotFoundError`, not a `TacoError` |
| `zero-byte assets are not allowed: <path>` | Empty payload |
| `extension group 'x' depends on the other rows of its batch` | Non-row-local extension output |
| `publication failed and could not be restored: ...; backup: <dir>` | A publish failed mid-way. The backup directory holds the previous files |

A build that fails leaves the previous dataset untouched, and the staging directory is
removed when the writer closes. If you need to inspect a failed build, keep the writer
open rather than exiting the `with` block.

### Environment variables

| Variable | Used by | Effect |
| --- | --- | --- |
| `TACO_LIB` | Python, Julia | Path to `libtaco` |
| `TACO_CACHE_DIR` | core | Metadata cache root |
| `TACO_CACHE_SIZE` | core | Cache cap in bytes, default 10 GiB |
| `TACO_CACHE_REFRESH` | core | Any non-empty value rebuilds touched entries |
| `TACO_CORE_DIR` | Makefile, Julia tests | Source directory of the development core |

## Repository layout

```
core/            C++23 reader: src/, include/taco/taco.h, tests/, vcpkg.json
extern/karu/     byte-access submodule (HTTP, S3, GCS, Azure, HF, Source Coop)
python/          taco-eo: taco/, tests/, examples/, pyproject.toml
r/               R source package; carries a vendored copy of core/ and karu
julia/           Taco.jl, with Artifacts.toml pointing at released native archives
javascript/      @asterisk-labs/taco, an independent pure-JS reader
docs/            website sources: spec/SPEC.md, api/, playground/, onepager/
tools/           sync_r_core.py and release helpers
Makefile         core, python, r, julia, javascript, site, clean
CHANGELOG.md     one file for the core and all five packages
```

Python package structure:

```
taco/contract/    Contract, Collection, Sample/Asset/Folder, schema, types, naming
taco/metadata/    scoped Pydantic groups, Extension base, profiles, MajorTOM, GeoEnrich
taco/extensions/  writer-time operations bound into a Level
taco/writer/      open_writer, archive and folder writers, metadata tables, partitions,
                  catalog (consolidate), export
taco/reader/      open_dataset, read, Dataset.sql, inspect, the cffi binding
taco/container/   cozip bridge, parquet options, atomic publish, local DatasetView
taco/validate.py  the full physical validator
taco/_repr.py     notebook HTML, with _graph.py for the structure diagram
```

`container/view.py` is a **local, pyarrow-based** reader used by `validate`, append and
`consolidate`. It is not the query path; `reader/` is. Do not confuse the two.

## Tests

`python/tests/` holds `conftest.py` (contract, collection, sample factory, archive and
folder fixtures, plus the autouse `isolated_cache`) and `datasets.py` (a table of
`DatasetCase` fixtures covering rich types, nested models, vectors and blobs). Tests
are grouped by surface: `test_contract`, `test_metadata`, `test_types`, `test_writer`,
`test_writer_cases`, `test_reader`, `test_core_reader`, `test_tacocat`, `test_export`,
`test_publish`, `test_validate`, `test_parquet`, `test_collection`, `test_progress`,
`test_examples`, and one per extension.

`test_examples.py` runs every script in `python/examples/`, so an example is a test.
Keep examples self-contained and synthetic, and validate every dataset either in the
example or explicitly in its test.

The `integration` marker covers cases needing installed optional runtimes and real
files. `core/tests/test_core.cpp` is the ctest target, with fixtures generated by
`core/tests/generate_fixtures.py`.

## Known sharp edges

- `Contract.levels` and the core's level list are ordered differently. See reading.md.
- A source list does not check `id` uniqueness; `consolidate` does.
- Extension descriptors are never written to `COLLECTION.json`; see extensions.md.
- A variable-sequence name with a leading zero reports a count error rather than
  naming the file. See contract.md.
- `taco.export` and the Python writer depend on `cozip._taco`, the private writer ABI
  in the `cozip` package. Keep `cozip` at or above the pin in `python/pyproject.toml`;
  `make python` installs the local checkout from `COZIP_PYTHON`.
