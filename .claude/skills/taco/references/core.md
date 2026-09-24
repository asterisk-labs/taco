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

`core/include/taco/taco.h`, ABI version **2**. The Python binding declares the same
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
const char*  taco_dataset_derived(const taco_dataset*);      // NULL when absent

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

Progress is a callback `(phase, done, total, user)` running on the working thread;
`total` is 0 while unknown, and `NULL` restores the built-in bar that writes to stderr
only when stderr is a terminal. Python installs a tqdm callback keyed by phase and
swallows every exception, so a broken bar can never break a download.

## Opening a container

`open_dataset` in `core/src/dataset.cpp` decides by shape, not by suffix:

- an object whose name ends in `.tacocat`, or a directory containing
  `.tacocat/COLLECTION.json`, is a catalog;
- a directory with `COLLECTION.json` is FOLDER or TACOCAT depending on whether the
  collection declares `taco:sources`;
- anything else is probed as a CoZIP archive and must report profile 2.

The contract is then checked: a supported `taco:version`, a non-empty
`taco:structure` of strings with parseable variable leaves, a `taco:metadata` object
whose levels match the Parquet files exactly, `sample` present, `children` present
whenever child levels exist, and every `children/...` level preceded by its parent.

For a ZIP the core reads the byte-zero index and fetches `COLLECTION.json` plus every
indexed Parquet range in one batch. For a remote directory it fetches the files named
by `taco:metadata`; it never lists a directory, because a bucket or repository root
cannot be listed reliably. `COLLECTION.json` larger than 64 MiB is refused.

## The cache

`core/src/cache.cpp`. An entry is `<label>-<hash>/` under the cache root, laid out like
a TACO FOLDER without `DATA/`: `COLLECTION.json`, `METADATA/`, and a `taco-cache.json`
stamp recording source, container, validation key, size and timestamps. The label
names the dataset for humans; the hash names the source for lookups.

Validation keys: a remote source uses the origin-reported object size, a local archive
its size and modification time. A failed probe fails the open rather than serving
metadata that cannot be validated. `TACO_CACHE_REFRESH` rebuilds, `TACO_CACHE_SIZE`
caps the root at 10 GiB by default and evicts least-recently-opened entries first, and
`TACO_CACHE_DIR` moves the root. Local FOLDER and TACOCAT are read in place.

`TACO_CACHE_SIZE` takes plain bytes: `TACO_CACHE_SIZE must be a number of bytes, got
4GB`.

## SQL generation

`core/src/sql.cpp` builds one statement. Every level becomes a CTE `l0`, `l1`, ...
over `read_parquet`, with a `COLUMNS(lambda c: ...)` projection that drops the
reserved reader names. Three shapes come out:

- **level**: `SELECT <projection> FROM read_parquet('<level>.parquet')`, the raw view.
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

A source list wraps each dataset's query in a `UNION ALL BY NAME`, adds a label and an
order column, and assigns the public index with
`dense_rank() OVER (ORDER BY internal_source_order, "taco:sample_index") - 1`. `idx`
filtering moves to the outer query so it applies to the global index.

## Thread safety and shutdown

The Python binding loads the library once under a lock and keeps one DuckDB connection
per thread. `NativeDataset` wraps the handle in `ffi.gc(..., taco_close)`, so closing
follows the Python object. The transport client is thread-local; call `taco_shutdown`
before unloading the library.
