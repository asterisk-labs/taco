/*
 * core/libzip/config.h
 *
 * Hand-written, cross-platform replacement for the config.h that
 * libzip's CMake build normally generates from cmake-config.h.in.
 *
 * Vendored from libzip 1.11.4. When bumping libzip, diff this file
 * against the new cmake-config.h.in and update the macro list to
 * match.
 *
 * Layout:
 *   1. Common (every platform, no #ifdef guard).
 *   2. POSIX (Linux, macOS, MinGW; explicitly NOT MSVC).
 *   3. Windows MSVC cluster — _underscore variants, Annex K, etc.
 *   4. macOS / BSD extras — arc4random, clonefile, getprogname, fts.
 *   5. Tunables — package metadata, sizeof checks, FDOPEN toggle.
 *
 * libzip itself does not depend on the actual values of PACKAGE or
 * VERSION, so we hardcode them to identify the vendored copy.
 */

#ifndef HAD_CONFIG_H
#define HAD_CONFIG_H

#ifndef _HAD_ZIPCONF_H
#include "zipconf.h"
#endif


/* ---- 1. Common POSIX (Linux, macOS, WASM/Emscripten) ---- */

#define HAVE_FILENO
#define HAVE_FCHMOD
#define HAVE_FSEEKO
#define HAVE_FTELLO
#define HAVE_SETMODE
#define HAVE_SNPRINTF
#define HAVE_STRCASECMP
#define HAVE_STRDUP
#define HAVE_STRTOLL
#define HAVE_STRTOULL
#define HAVE_STDBOOL_H
#define ENABLE_FDOPEN

/* POSIX-only macros — guarded to skip MSVC, where they don't apply.
 * MinGW provides POSIX semantics and is treated as POSIX here.
 *
 *   HAVE_UNISTD_H   leaks into zlib's zconf.h line 440 (no _WIN32
 *                   guard upstream), making MSVC try to include
 *                   <unistd.h> and fail C1083. Must NOT be set on MSVC.
 *   HAVE_STRINGS_H  POSIX <strings.h> is absent on MSVC (different
 *                   from <string.h>). Guarded preventively.
 *   HAVE_LOCALTIME_R  POSIX-only thread-safe localtime. MSVC has
 *                     localtime_s instead. compat.h dispatches via
 *                     these flags; setting HAVE_LOCALTIME_R on MSVC
 *                     would resolve to the missing symbol. */
#if !defined(_WIN32) || defined(__MINGW32__) || defined(__MINGW64__)
#define HAVE_LOCALTIME_R
#define HAVE_STRINGS_H
#define HAVE_UNISTD_H
#endif

/* Sizes: 64-bit on every platform we target (no 32-bit Windows,
 * no 32-bit ARM). If 32-bit support is ever required, gate these
 * with #if SIZEOF_VOID_P == 8. */
#define SIZEOF_OFF_T  8
#define SIZEOF_SIZE_T 8


/* ---- 2. Windows MSVC ---- */

#if defined(_WIN32) && !defined(__MINGW32__) && !defined(__MINGW64__)

/* MSVC ships its POSIX-ish APIs with a leading underscore. */
#define HAVE__CLOSE
#define HAVE__DUP
#define HAVE__FDOPEN
#define HAVE__FILENO
#define HAVE__FSEEKI64
#define HAVE__FSTAT64
#define HAVE__SETMODE
#define HAVE__SNPRINTF
#define HAVE__SNPRINTF_S
#define HAVE__SNWPRINTF_S
#define HAVE__STAT64
#define HAVE__STRDUP
#define HAVE__STRICMP
#define HAVE__STRTOI64
#define HAVE__STRTOUI64
#define HAVE__UNLINK

/* MSVC time API. localtime_s on MSVC has signature
 * (struct tm *result, const time_t *t) and returns errno_t.
 * compat.h's HAVE_LOCALTIME_S branch handles the swapped argument
 * order on Windows. */
#define HAVE_LOCALTIME_S

/* C11 Annex K bounds-checking interfaces. MSVC declares these in
 * <string.h> / <corecrt_memcpy_s.h>. They MUST be advertised here
 * so libzip's compat.h does not replace them with `#define`-based
 * macro fallbacks — those macros pollute the identifier namespace
 * and clash with the CRT's own function declarations, producing
 * `error C2059: syntax error: '('` deep inside corecrt headers.
 *
 * On non-MSVC platforms (glibc, macOS, MinGW) these symbols are
 * absent from the CRT and compat.h's macro fallbacks are correct. */
#define HAVE_MEMCPY_S
#define HAVE_STRNCPY_S
#define HAVE_STRERROR_S
#define HAVE_STRERRORLEN_S

#endif /* _WIN32 && !MINGW */


/* ---- 3. Apple/BSD extras ---- */

#if defined(__APPLE__)
#define HAVE_ARC4RANDOM     /* BSD-style RNG, present on Darwin */
#define HAVE_CLONEFILE      /* APFS copy-on-write copy */
#define HAVE_GETPROGNAME    /* BSD-style argv[0] accessor */
#define HAVE_FTS_H          /* file traversal; also on glibc but
                               libzip checks it on macOS */
#endif


/* ---- 4. Identification ---- */

#define PACKAGE "cozip-vendored-libzip"
#define VERSION "1.11.4"

#endif /* HAD_CONFIG_H */