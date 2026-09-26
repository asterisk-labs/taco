# Versioning and releasing

Sources: `CHANGELOG.md`, `docs/RELEASING.md`, `.github/workflows/`,
`docs/spec/SPEC.md` Annex A, `Makefile`.

## Two version lines

The **specification** is versioned on its own: this is spec `3.0.0`, stored in every
dataset as `taco:version`, and a reader must reject a version it does not support.
The **implementations** share one release number and declare which specification they
support.

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
