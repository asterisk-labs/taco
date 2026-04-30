#!/bin/sh
# Generate src/version.h from cozip/VERSION.
# Mirrors what core/CMakeLists.txt does at configure time, but
# portable for r-universe / R CMD INSTALL builders that don't run
# CMake.
#
# Usage: sh gen-version.sh <path/to/VERSION> <path/to/version.h>

set -e

VERSION_FILE="${1:?missing VERSION input path}"
OUTPUT_FILE="${2:?missing version.h output path}"

if [ ! -f "$VERSION_FILE" ]; then
    echo "gen-version.sh: $VERSION_FILE not found" >&2
    exit 1
fi

VERSION=$(tr -d '[:space:]' < "$VERSION_FILE")
MAJOR=$(echo "$VERSION" | cut -d. -f1)
MINOR=$(echo "$VERSION" | cut -d. -f2)
PATCH=$(echo "$VERSION" | cut -d. -f3)

cat > "$OUTPUT_FILE" <<EOF
#ifndef COZIP_VERSION_H
#define COZIP_VERSION_H
#define COZIP_VERSION_MAJOR ${MAJOR}
#define COZIP_VERSION_MINOR ${MINOR}
#define COZIP_VERSION_PATCH ${PATCH}
#define COZIP_VERSION_STRING "${VERSION}"
#ifdef __cplusplus
extern "C" {
#endif
#include "cozip.h"
COZIP_API const char* cozip_version_string(void);
#ifdef __cplusplus
}
#endif
#endif /* COZIP_VERSION_H */
EOF

echo "Generated $OUTPUT_FILE from $VERSION_FILE ($VERSION)"