# Compatibility, versioning and releasing

Sources: `CHANGELOG.md`, `docs/RELEASING.md`, `.github/workflows/`,
`docs/spec/SPEC.md` Annex A, `Makefile`.

## Two version lines

The **specification** is versioned on its own: this is spec `3.0.0`, stored in every
dataset as `taco:version`, and a reader must reject a version it does not support.
The **implementations** share one number, currently `0.11.0`, and declare which
specification they support.

The C API has a third number, `TACO_API_VERSION`, currently `2`. The Python binding
refuses a library reporting anything else: `the TACO core at <path> has C API 1,
expected 2`.

## What a change costs

| Change | Consequence |
| --- | --- |
| Adding a metadata field, or reorganizing the structure | A different dataset. Needs a new `id`, cannot be appended to the old one |
| Changing the meaning of an existing field | A different dataset, even with the same schema |
| Appending samples, or correcting values | Same `id`, provided contract and semantics hold |
| Changing the physical layout, internal columns or generated names | Every reader and `SPEC.md` change together |
| Changing a semantic extension parameter | Rejected on append, because two meanings would share one column |

TACO deliberately trades flexibility for a shared contract. It is the wrong format
when samples must evolve independently.

## Recent surface changes worth knowing

- **0.11.0**: `Contract(metadata=...)` takes a list of `taco.Level`; **`taco.MetadataSchema`
  was removed**. `Collection` takes metadata as keyword groups (`labels=...`) instead of
  `metadata=taco.CollectionMetadata(...)`. The spatial and temporal profiles use STAC
  fields (`datetime`, `geometry`, `bbox`, `proj_code`, `proj_shape`, `proj_transform`);
  `ISpatial`, `ISTAC` and `taco.extensions.Temporal` are gone, and datasets written with
  the old profiles must be rebuilt.
- **0.10.3**: `GeoEnrich` defaults to the public MajorTOM index on Source Cooperative;
  Earth Engine needs `backend="earthengine"`.
- **0.10.2**: filtered `dataset` queries resolve file columns only for matching
  samples; exports rebuild in bounded batches; R and Julia reuse native handles.
- **0.10.1**: `taco.export()` **requires** `sql`. Python datasets reuse their native
  handle across queries.
- **0.10.0**: `Dataset.sql()` exposes one `dataset` relation with the same wide shape
  as `read()`; raw levels stay available by name. **The `data` and `files` relations
  were removed** — a query written against them breaks here.
- **0.9.0**: `Sample` gained a required `id`, stored in `sample.parquet`, unique
  across the dataset and its partitions. Row position remains the physical identity.
- **0.8.1**: wide reads adopted `{file}::location` and `{file}::header`, the second
  colon keeping them apart from a metadata field like `image:location`. `tasks`
  became optional. `MajorTOM` codes gained the `MT` prefix (`MT10km_0770U_0395R`).

Check `CHANGELOG.md` before assuming an older snippet still runs; it covers the core
and all five packages in one file.

## Migration from v2

v3 is **not** compatible with v2, and there is no automatic migration tool. Define a
v3 contract and rebuild with `taco`; the GeoTIFF, NetCDF and other payloads can stay
byte-identical, only the packaging and metadata are rewritten.

| Aspect | v2 | v3 |
| --- | --- | --- |
| Contract | Inferred from the first sample at runtime (PIT) | Declared before any data is written |
| Abstractions | SAMPLE, TORTILLA, TACO | Contract, Collection, Sample, Folder, Asset |
| Identity | String ids | `id` for logical identity, local integer indices for position |
| Irregular structures | `__TACOPAD__` placeholders | Variable sequences `prefix*[a,b].ext` |
| Metadata | Consolidated `levelX.parquet` plus per-folder `__meta__` | One Parquet per level, no local metadata |
| Parquet naming | By depth (`level0.parquet`) | By row level (`children__before.parquet`) |
| Reader | Python, three container backends | One C++ core for Python, R and Julia; an independent browser reader for JavaScript |
| Writer | TacoToolbox classes | `taco.open_writer()` |
| ZIP | `.tacozip` | `.zip`, identified by the CoZIP profile byte |
| TACOCAT | Binary file, 128-byte header | Directory of merged Parquet plus `COLLECTION.json` |
| TACOLLECTION | Separate JSON | Merged into TACOCAT via `taco:sources` |
| Filtering | `filter_bbox()`, `filter_datetime()` | SQL `WHERE` on Parquet columns |
| Concatenation | `concat()` | SQL `UNION` or TACOCAT consolidation |
| Extensions | `extend_with()` with base classes | Scoped Pydantic groups plus `Extension` |

Annex A of `SPEC.md` has the full narrative.

## Releasing

The core and the Python, R, Julia and JavaScript packages share one version. Keep it
synchronized in:

```
core/CMakeLists.txt          core/vcpkg.json
python/pyproject.toml        r/DESCRIPTION            r/configure.ac
julia/Project.toml           javascript/package.json  javascript/package-lock.json
```

Move the pending `CHANGELOG.md` entries under the version being released **before**
either tag.

Julia needs checksums for the native archives in the source tree, so a release uses
**two tags**:

1. Tag the reviewed commit `libtaco-vX.Y.Z`. The release workflow builds and publishes
   the five native archives, then commits an updated `julia/Artifacts.toml` to `main`.
2. Review that generated commit and tag it `vX.Y.Z`. The workflow refuses this tag
   unless all five artifact entries point at `libtaco-vX.Y.Z`.

The final tag tests the installed Julia package **without** `TACO_LIB` before
publishing to PyPI and npm, which proves the native library resolves on every
supported platform. A manual workflow run builds and tests packages but publishes
nothing; use it as a dry run.

## Local checks

```bash
make core        # cmake, build, ctest, stage libtaco for Python
make python      # install, ruff format and check, mypy strict, pytest with coverage, build the wheel
make r           # sync the vendored core, roxygen, testthat
make julia       # Pkg.test against the development core
make javascript  # npm ci, types, tests, pack dry run
make site        # compile docs/ into _site/
make clean
```

`make python` installs `duckdb==1.5.5`, the local cozip checkout (`COZIP_PYTHON`,
default `../cozip/python`) and `python[dev,test-eo]`. Ruff targets py310 at line
length 120 and mypy runs strict. Pytest runs with `filterwarnings = ["error"]` and
`xfail_strict`, so a new warning fails the suite.

CI has four workflows: **Python** (a compatibility job and a full job), **JavaScript**,
**Pages** (builds and deploys the site) and **Release**, which validates, builds the
native library across a platform matrix and publishes.
