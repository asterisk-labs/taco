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
 *   1. Common POSIX macros — apply on Linux, macOS, WASM/Emscripten.
 *   2. Windows MSVC cluster — _underscore variants of the same APIs.
 *   3. macOS / BSD extras — arc4random, clonefile, getprogname, fts.
 *   4. Tunables — package metadata, sizeof checks, FDOPEN toggle.
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
#define HAVE_LOCALTIME_R
#define HAVE_SETMODE
#define HAVE_SNPRINTF
#define HAVE_STRCASECMP
#define HAVE_STRDUP
#define HAVE_STRTOLL
#define HAVE_STRTOULL
#define HAVE_STDBOOL_H
#define HAVE_STRINGS_H
#define HAVE_UNISTD_H
#define ENABLE_FDOPEN

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

/* MSVC has these in its CRT but not under POSIX names. The
 * common-block #defines above are also valid on MSVC; libzip's
 * compat.h handles the alias. */

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