#!/usr/bin/env bash
#
# Syncs cozip/VERSION to every binding's metadata file.
#
# cozip/VERSION is the single source of truth for the cozip release
# version across all bindings (C library, Python wheel, R package, ...).
# This script reads that file and propagates its value to:
#
#   - cozip/r/DESCRIPTION         (Version: field)
#   - cozip/python/pyproject.toml (version field)  [if present]
#
# Usage:
#   tools/sync_version.sh             # propagate current cozip/VERSION
#   tools/sync_version.sh 2026.05.01  # write new version, then propagate
#
# Run after every version bump and commit the result alongside the
# VERSION change. The version-sync CI workflow will reject any push
# where the values disagree.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT/cozip/VERSION"
R_DESC="$ROOT/cozip/r/DESCRIPTION"
PY_TOML="$ROOT/cozip/python/pyproject.toml"

# ---- 1. Determine the version ----

if [[ $# -ge 1 ]]; then
    NEW_VERSION="$1"
    echo "$NEW_VERSION" > "$VERSION_FILE"
    echo "Wrote $NEW_VERSION to $VERSION_FILE"
fi

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "ERROR: $VERSION_FILE not found" >&2
    exit 1
fi

VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"

if [[ -z "$VERSION" ]]; then
    echo "ERROR: $VERSION_FILE is empty" >&2
    exit 1
fi

# Validate R-compatible format (sequence of integers separated by . or -).
# CRAN allows e.g. "2026.4.29" or "1.0.0". Rejects "v1.0", "1.0-rc1", etc.
if ! [[ "$VERSION" =~ ^[0-9]+([.-][0-9]+)+$ ]]; then
    echo "ERROR: '$VERSION' is not a valid R package version." >&2
    echo "       Must match ^[0-9]+([.-][0-9]+)+\$ (e.g. 2026.4.29 or 1.0.0)" >&2
    exit 1
fi

echo "Syncing version: $VERSION"

# ---- 2. Portable in-place sed wrapper ----

# GNU sed wants `sed -i ...`, BSD sed (macOS) wants `sed -i '' ...`.
# Wrapping it in a function keeps the call sites readable.
sed_inplace() {
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# ---- 3. Update R DESCRIPTION ----

if [[ -f "$R_DESC" ]]; then
    sed_inplace -E "s/^Version:.*/Version: $VERSION/" "$R_DESC"
    echo "  Updated $R_DESC"
else
    echo "  Skipped R DESCRIPTION (not found)"
fi

# ---- 4. Update Python pyproject.toml ----
#
# Skip cleanly if the project uses PEP 621 dynamic versioning. In that
# mode the version is resolved at build time from cozip/VERSION via a
# tool like scikit-build-core / hatch-vcs / setuptools-scm, so there
# is no literal `version = "..."` to rewrite.

if [[ -f "$PY_TOML" ]]; then
    if grep -qE '^[[:space:]]*dynamic[[:space:]]*=.*"version"' "$PY_TOML"; then
        echo "  Skipped $PY_TOML (dynamic version)"
    elif grep -qE '^version[[:space:]]*=' "$PY_TOML"; then
        # Matches both:   version = "1.0.0"
        #                 version="1.0.0"
        # inside the [project] table. Doesn't touch dependency version specs.
        sed_inplace -E "s/^version[[:space:]]*=[[:space:]]*\"[^\"]*\"/version = \"$VERSION\"/" "$PY_TOML"
        echo "  Updated $PY_TOML"
    else
        echo "  Skipped $PY_TOML (no [project].version found)"
    fi
else
    echo "  Skipped pyproject.toml (not found)"
fi

echo "Done."