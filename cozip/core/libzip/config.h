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
 *   3b. MinGW/Rtools cluster — narrow set of Windows-specific
 *       declarations needed when building with GCC.
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
#define HAVE_SETMODE
#define HAVE_SNPRINTF
#define HAVE_STRDUP
#define HAVE_STRTOLL
#define HAVE_STRTOULL
#define HAVE_STDBOOL_H
#define ENABLE_FDOPEN

/* POSIX-only — guarded to skip MSVC, which lacks these.
 * MinGW provides POSIX semantics and is treated as POSIX here.
 *
 * libzip's compat.h uses each HAVE_* macro as a "capability" flag:
 * declaring HAVE_X tells compat.h that X is callable. If MSVC doesn't
 * have X but config.h advertises HAVE_X, compat.h skips the fallback
 * alias and the source files break with "undeclared identifier".
 *
 * Headers (<unistd.h>, <strings.h>) likewise must not be advertised
 * on MSVC because compat.h / zlib's zconf.h #include them blindly. */
#if !defined(_WIN32) || defined(__MINGW32__) || defined(__MINGW64__)
#define HAVE_FCHMOD       /* POSIX file permission API */
#define HAVE_FSEEKO       /* 64-bit fseek; MSVC has _fseeki64 instead */
#define HAVE_FTELLO       /* 64-bit ftell; MSVC has _ftelli64 instead */
#define HAVE_LOCALTIME_R  /* MSVC has localtime_s (different signature) */
#define HAVE_STRCASECMP   /* MSVC has _stricmp instead */
#define HAVE_STRINGS_H    /* POSIX <strings.h>, distinct from <string.h> */
#define HAVE_UNISTD_H     /* POSIX <unistd.h>, leaks into zlib's zconf.h */
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
 * NOTE: strerrorlen_s is intentionally NOT advertised. It is
 * optional in Annex K and MSVC does not implement it. Letting
 * compat.h see HAVE_STRERROR_S without HAVE_STRERRORLEN_S triggers
 * a fallback macro `#define strerrorlen_s(errnum) 8192` which is
 * the correct behavior on MSVC.
 *
 * On non-MSVC platforms (glibc, macOS, MinGW) these symbols are
 * absent from the CRT and compat.h's macro fallbacks are correct. */
#define HAVE_MEMCPY_S
#define HAVE_STRNCPY_S
#define HAVE_STRERROR_S

#endif /* _WIN32 && !MINGW */


/* ---- 2b. MinGW / Rtools45 (Windows with GCC) ---- */

/* MinGW-w64 ships <wchar.h> with the C11 Annex K wide-string family,
 * including _snwprintf_s. We must advertise it here so libzip's
 * compat.h skips its variadic-macro fallback at line 104. That
 * fallback is `#define _snwprintf_s(buf, bufsz, len, fmt, ...) ...`
 * which GCC under -std=gnu2x rejects with "expected declaration
 * specifiers or '...' before '(' token" when expanded by libzip's
 * own callers — even though the macro itself is syntactically
 * valid C99. Declaring HAVE__SNWPRINTF_S routes libzip to the real
 * CRT function and avoids the macro entirely. */
#if defined(__MINGW32__) || defined(__MINGW64__)
#define HAVE__SNWPRINTF_S
#endif


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