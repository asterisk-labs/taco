# The C++ core

Sources: `core/`, `python/taco/reader/native.py`, SPEC 8.2.

`libtaco` is the reader. It resolves a container, copies the metadata it needs into a
cache, and generates DuckDB SQL. It executes nothing: each binding runs the SQL with
its own DuckDB. Python, R and Julia load this library; the JavaScript reader is a
separate implementation.

C++23, one shared library, hidden visibility so only `TACO_API` symbols escape. It
links `karu` (byte access) from `extern/karu`, and vcpkg supplies static curl and
OpenSSL for release builds.

## Building

```bash
make core          # cmake configure, build, ctest, then stage the library for Python
```

`make core` writes to `core/build/` and copies `libtaco.{dylib,so}` into
`python/taco/_lib/`, which is where the Python binding looks. It exports
`TACO_CORE_DIR` and `TACO_LIB` for the Julia tests, which link the development build.
R compiles its own vendored copy; refresh it with `make sync-r-core`.

Useful options: `-DTACO_BUILD_TESTS=ON` (the `core` ctest target),
`-DTACO_WERROR=ON` (warnings as errors, what CI uses),
`-DCMAKE_BUILD_TYPE=Debug`, `-DOPENSSL_ROOT_DIR=$(brew --prefix openssl@3)`,
`-DTACO_KARU_DIR=...`.

The project version lives in `core/CMakeLists.txt` and `core/vcpkg.json`, and
`TACO_API_VERSION` in `core/include/taco/taco.h`. CMake reads the header and fails the
configure if that define is missing.

`core/tests/test_c_header.c` compiles the public header as C11 in every test build,
because R and Julia bind it as C.

## The C API

`core/include/taco/taco.h`, C API version **3**. The Python binding declares the same
cdef in `reader/native.py` and refuses a library reporting another version.

```c
int          taco_api_version(void);
const char*  taco_version_string(void);
const char*  taco_last_error(void);          // thread-local, last failed call
void         taco_free(char* text);          // frees a char** out-parameter
void         taco_shutdown(void);            // release this thread's transport

void         taco_set_progress(taco_progress_fn cb, void* user);

taco_status  taco_open(const char* source, const char* cache_dir, taco_dataset** out);
void         taco_close(taco_dataset* dataset);
const char*  taco_dataset_source(const taco_dataset*);
const char*  taco_dataset_container(const taco_dataset*);    // "zip" | "folder" | "tacocat"
const char*  taco_dataset_collection(const taco_dataset*);   // COLLECTION.json text
size_t       taco_dataset_level_count(const taco_dataset*);
const char*  taco_dataset_level(const taco_dataset*, size_t);
size_t       taco_dataset_structure_count(const taco_dataset*);
const char*  taco_dataset_structure(const taco_dataset*, size_t);

taco_status  taco_sql(const taco_dataset* const* datasets, size_t count,
                      const taco_read_options* options, char** out_sql);
taco_status  taco_profile(const char* source, char** out_name);
taco_status  taco_fetch(const taco_fetch_item* items, size_t count);
```

Ownership: `const char*` getters return views owned by the dataset, valid until
`taco_close`. Anything returned through a `char**` must be released with `taco_free`.
Status is `TACO_OK`, `TACO_ERR_INVALID`, `TACO_ERR_IO`, `TACO_ERR_NOT_FOUND` or
`TACO_ERR_INTERNAL`, and the message comes from `taco_last_error` on the same thread.

`taco_read_options`:

| Field | Meaning |
| --- | --- |
| `idx` | `NULL` for all samples, `"5"` for one, `"[0, 100]"` for a half-open range |
| `level` | `NULL` for the joined view, otherwise one contract level read raw |
| `pivoted` | non-zero for one row per sample (wide), zero for one row per file (long) |
| `files` / `file_count` | structure leaves to read, or `NULL` for all |
| `location` | non-zero to compute file locations |

`files` with `level` set is refused: `taco: files does not apply when level is set`.

`taco_fetch` copies byte ranges of local or remote objects into files, creating parent
directories; a zero length reads to the end. `taco.export` uses it to download
payloads. Objects are read in 8 MiB chunks so memory stays bounded.

Progress callbacks run on the working thread as `(phase, done, total, user)`;
`total` is 0 while unknown. Passing `NULL` restores the built-in bar, which writes
only to terminal stderr. Python uses tqdm and suppresses callback errors so they
do not interrupt downloads.

## Opening a container

`open_dataset` in `core/src/dataset.cpp` decides by shape, not by suffix:

- an object whose name ends in `.tacocat`, or a directory containing
  `.tacocat/COLLECTION.json`, is a catalog;
- a directory with `COLLECTION.json` is FOLDER or TACOCAT depending on whether the
  collection declares `taco:sources`;
- anything else is probed as a CoZIP archive and must report profile 2.

The contract is then checked: a supported `taco:version`, a non-empty
`taco:structure` of strings with parseable variable leaves, a `taco:metadata` object
whose levels map one to one onto the Parquet files, `sample` present, `children` present
whenever child levels exist, every `children/...` level preceded by its parent, and
`rumi` fields limited to the header and statistics. Their types and optional file
selections are checked here as well. `core/src/rumi.hpp` holds the statistic grammar
shared with the SQL builder.

`declared_levels` takes the levels from `taco:metadata` and computes each filename
with `level_to_file`. It never reads a level back from a filename, because
`children/a_/b` and `children/a/_b` share one, and it refuses two levels whose
filenames are equal ignoring case. `match_files` then fails on a missing or a stray
Parquet.

For a ZIP the core reads the byte-zero index and fetches `COLLECTION.json` plus every
indexed Parquet range in one batch. For a remote directory it fetches the files named
by `taco:metadata`; it never lists a directory, because a bucket or repository root
cannot be listed reliably. `COLLECTION.json` larger than 64 MiB is refused.

## The cache

`core/src/cache.cpp`. An entry is `<label>-<hash>/` under the cache root, laid out like
a TACO FOLDER without `DATA/`: `COLLECTION.json`, `METADATA/`, and a `taco-cache.json`
stamp recording source, container, validation key, size and timestamps. The label
names the dataset for humans; the hash names the source for lookups.

Processes that open one source together all build it. Each writes a `.tmp-` staging
directory and renames it into place. When the rename finds a complete entry with the
same key and container, that entry is kept and the staging copy deleted. An outdated
entry, or any entry under `TACO_CACHE_REFRESH`, is swapped in one step
(`renamex_np` with `RENAME_SWAP` on macOS, `renameat2` with `RENAME_EXCHANGE` on
Linux) or else moved aside to a `.trash-` directory first, for up to 8 attempts.
Cleanup and eviction skip the `.tmp-` and `.trash-` directories of other processes.

Validation keys: a remote ZIP uses the 8-byte integrity hash at bytes 43-50 of its
CoZIP header (`read_cozip_integrity`, `remote_zip_key`), read with one `bytes=43-50`
request, because cozip pads every small archive to 32,819 bytes and a size cannot tell
two of them apart. A remote FOLDER or TACOCAT uses the origin-reported sizes of its
metadata files, since Karu reports no ETag or modification time. A local ZIP
never enters the cache: its process owns a temporary metadata directory. A failed
probe fails the open rather than serving metadata that cannot be validated.
`TACO_CACHE_REFRESH` rebuilds entries. `TACO_CACHE_SIZE` caps the root at 5 GiB by
default and evicts the least recently opened entries first. `TACO_CACHE_DIR` moves
the root. Local FOLDER and TACOCAT are read in place.

`TACO_CACHE_SIZE` takes plain bytes: `TACO_CACHE_SIZE must be a number of bytes, got
4GB`.

Snapshots: an open never reads the entry itself. It links the entry's files (hard
links, copies where links fail) into `.open-<host>-<pid>-<n>/` under the root, checks
the stamp again so a concurrent rebuild counts as a miss, and points the SQL there. A
process-wide registry, like the one for local ZIP extractions, keeps one snapshot per
entry version, reuses it on later opens, and removes it at exit, only in the process
that made it (a forked child clears its copy of the registry without deleting).
`store` removes the snapshots of dead processes on the same host; the host hash keeps
a cache on NFS from judging another machine's pids. Eviction and size accounting skip
`.open-`, so an evicted or refreshed entry never breaks an open dataset, and SQL from
`native_sql` stays valid until the process exits. A read-only cache is read in place.

## SQL generation

`core/src/sql.cpp` builds one statement. Every level becomes a CTE `l0`, `l1`, ...
over `read_parquet`, with a `COLUMNS(lambda c: ...)` projection that drops the
reserved reader names. `parquet_scan` writes every scan as
`read_parquet('<path>', hive_partitioning = false)`, the path passed through
`glob_escape` (`core/src/paths.cpp`), which turns `[`, `]`, `?` and `*` into
`[[]`, `[]]`, `[?]` and `[*]`. Without it `run[1]/` would also match `run1/`, and a
folder such as `id=7/` in the path would replace the `id` column. Three shapes come
out:

- **level**: `SELECT <projection> FROM read_parquet('<level>.parquet', hive_partitioning = false)`, the raw view.
- **flat (long)**: one `UNION ALL BY NAME` branch per child level, each joined up to
  the sample level, projecting `path`, `taco:location` and the ancestor columns.
  `ancestor_projection` walks child to sample and keeps the first occurrence of each
  field name, so a deeper level shadows a shallower one; the result is sorted by name.
- **pivot (wide)**: the sample level plus a `LEFT JOIN LATERAL` that pivots the child
  branches for that one sample. A fixed leaf becomes
  `MAX(CASE WHEN path = '<decl>' THEN "taco:location" END)`; a variable leaf becomes
  `list(... ORDER BY TRY_CAST(regexp_extract(path, '^prefix(0|[1-9][0-9]*)suffix$', 1)
  AS BIGINT)) FILTER (...)`. That regex is where "no leading zeros" is enforced at
  read time. `location=false` skips the child levels entirely and emits typed NULL
  placeholders, so a metadata-only read never touches a child Parquet.

Folder rows are excluded from file branches by `file_filter`, which lists the names
that have their own child level: a name is a folder exactly when a level exists below
it.

A raw level of a source list (`level_union`) shifts `internal:current_id` and
`internal:parent_id` by a materialized `taco_offsets` CTE, a `count(*)` per source and
a window prefix sum, so ids count across the list as in a TACOCAT and the parent join
stays within each source.

Locations: below an http(s) base, FOLDER paths go through `url_encode` with `/` and
`!*'()` put back, which is `encodeURIComponent` per component, and TACOCAT partition
names are encoded in C++ (`encode_uri_path`). A FOLDER row whose relative path is not
normalized, and a TACOCAT row whose `internal:source_file` is not listed in
`taco:sources` (a `CASE` over the listed files), fail the query through DuckDB's
`error()`, so no location leaves `DATA/` or the catalog directory. The long view
dedupes fields ignoring ASCII case, deepest level first, as DuckDB resolves names.

A source list wraps each dataset's query in a `UNION ALL BY NAME`, adds a label and an
order column, and assigns the public index with
`dense_rank() OVER (ORDER BY internal_source_order, "taco:sample_index") - 1`. `idx`
filtering moves to the outer query so it applies to the global index.

## Thread safety and shutdown

A `file://` source is percent-decoded into its local path (`file_uri_path` in
`paths.cpp`) for locations and for reads, so its locations are the local path's.
The JSON parser (`json.cpp`) refuses a repeated key in an object and a number beyond
the finite double range. A variable sequence matches only indices below its maximum
(`variable_index`, `variable_match` in `sql.cpp`).

The process-wide registries (`LocalExtractions`, `Snapshots`) hold their mutex only
for short sections and register `pthread_atfork` handlers: lock before the fork,
unlock in the parent, reinitialise in the child. The progress mutex stays held across
callbacks, so the child reinitialises it unless the forking thread is reporting.

The Python binding loads the library once under a lock and keeps one DuckDB connection
per thread. `NativeDataset` wraps the handle in `ffi.gc(..., taco_close)`, so closing
follows the Python object. The transport client is thread-local; call `taco_shutdown`
before unloading the library.

DuckDB is not fork-safe, so `python/taco/reader/engine.py` registers fork handlers: a
forked child keeps every inherited connection alive without using or closing it, and
opens its own on first use. Taco.jl wraps `taco_open`, `taco_profile` and
`taco_set_progress` in `@_gc_safe_ccall` (`@ccall gc_safe = true` on Julia 1.12, and
`jl_gc_safe_enter`/`jl_gc_safe_leave` before), so a task blocked on a lock of the core
does not stop a garbage collection, and its progress bar writes to stderr without
yielding.
