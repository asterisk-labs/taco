/*
 * cozip R bindings — .Call shims to libcozip.
 *
 * Two entries:
 *
 *   R_cozip_plan(names, sizes, in_idx) -> list(offset, size)
 *       Computes payload offsets and sizes for the given entries.
 *       Used by cozip::create() to learn user offsets before
 *       writing the __metadata__.parquet.
 *
 *   R_cozip_finalize(out_path, names, paths, sizes, in_idx, profile) -> NULL
 *       End-to-end pipeline: plan, build index payload, write
 *       archive, patch FNV-1a 64 integrity hash.
 *
 * The .Call surface is profile-agnostic. Profile-specific logic
 * (FLAT's __metadata__.parquet, TACO's COLLECTION.json + METADATA/)
 * lives in R/cozip.R.
 *
 * Memory: cozip_entry_t arrays and intermediate buffers are
 * allocated via R_alloc, so they free automatically on .Call return
 * (including longjmp out of Rf_error).
 */

#define R_NO_REMAP
#include <R.h>
#include <Rinternals.h>
#include <R_ext/Rdynload.h>
#include <R_ext/Visibility.h>

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "cozip.h"


/* ---- 1. Helpers ---- */

/* Throw an R error formatted from a libcozip status + error struct.
 * Rf_error is marked NORET in Rinternals.h, so the compiler treats
 * this macro as a non-returning expression.
 */
#define R_THROW_COZIP(status, err) \
    Rf_error("[cozip:%s] %s", \
             cozip_status_string(status), \
             (err).message[0] ? (err).message : "(no message)")

/* Allocate a bit64 integer64 vector of length n. The returned SEXP
 * is NOT protected; the caller must PROTECT it before any further
 * allocation.
 */
static SEXP alloc_integer64(R_xlen_t n) {
    SEXP x   = PROTECT(Rf_allocVector(REALSXP, n));
    SEXP cls = PROTECT(Rf_mkString("integer64"));
    Rf_setAttrib(x, R_ClassSymbol, cls);
    UNPROTECT(2);
    return x;
}

/* Type and length validators. Each one Rf_errors out on mismatch. */

static void check_chars(SEXP x, R_xlen_t expected_len, const char *who) {
    if (TYPEOF(x) != STRSXP)
        Rf_error("'%s' must be a character vector (got %s)",
                 who, Rf_type2char(TYPEOF(x)));
    if (Rf_xlength(x) != expected_len)
        Rf_error("'%s' must have length %lld (got %lld)",
                 who, (long long)expected_len, (long long)Rf_xlength(x));
}

static void check_doubles(SEXP x, R_xlen_t expected_len, const char *who) {
    if (TYPEOF(x) != REALSXP)
        Rf_error("'%s' must be a numeric vector (got %s)",
                 who, Rf_type2char(TYPEOF(x)));
    if (Rf_xlength(x) != expected_len)
        Rf_error("'%s' must have length %lld (got %lld)",
                 who, (long long)expected_len, (long long)Rf_xlength(x));
}

static void check_logicals(SEXP x, R_xlen_t expected_len, const char *who) {
    if (TYPEOF(x) != LGLSXP)
        Rf_error("'%s' must be a logical vector (got %s)",
                 who, Rf_type2char(TYPEOF(x)));
    if (Rf_xlength(x) != expected_len)
        Rf_error("'%s' must have length %lld (got %lld)",
                 who, (long long)expected_len, (long long)Rf_xlength(x));
}

/* Build a cozip_entry_t array from R-side vectors. Allocates from
 * R's transient pool.
 *
 * `paths` may be R_NilValue, in which case source.kind stays
 * COZIP_SOURCE_NONE on every entry — fine for cozip_plan, but
 * cozip_write_archive will reject it.
 *
 * NA in `paths[i]` also leaves source.kind = NONE for that entry.
 * cozip::create uses this for the __metadata__ placeholder slot
 * during the first plan call.
 */
static cozip_entry_t* build_entries(SEXP names, SEXP paths,
                                    SEXP sizes, SEXP in_idx,
                                    R_xlen_t n) {
    cozip_entry_t *entries =
        (cozip_entry_t*)R_alloc((size_t)n, sizeof(cozip_entry_t));
    memset(entries, 0, (size_t)n * sizeof(cozip_entry_t));

    for (R_xlen_t i = 0; i < n; i++) {
        SEXP name_sxp = STRING_ELT(names, i);
        if (name_sxp == NA_STRING)
            Rf_error("NA in 'names' at index %lld", (long long)(i + 1));
        entries[i].arc_name = Rf_translateCharUTF8(name_sxp);

        double sz = REAL(sizes)[i];
        if (!R_FINITE(sz) || sz < 0)
            Rf_error("Invalid size at index %lld (got %g)",
                     (long long)(i + 1), sz);
        entries[i].payload_size = (uint64_t)sz;

        int flag = LOGICAL(in_idx)[i];
        if (flag == NA_LOGICAL)
            Rf_error("NA in 'in_idx' at index %lld", (long long)(i + 1));
        entries[i].in_index = (bool)flag;

        if (paths != R_NilValue) {
            SEXP path_sxp = STRING_ELT(paths, i);
            if (path_sxp != NA_STRING) {
                entries[i].source.kind   = COZIP_SOURCE_PATH;
                entries[i].source.u.path = Rf_translateCharUTF8(path_sxp);
            }
        }
    }
    return entries;
}


/* ---- 2. .Call entries ---- */

SEXP R_cozip_plan(SEXP names, SEXP sizes, SEXP in_idx) {
    R_xlen_t n = Rf_xlength(names);
    check_chars(names, n, "names");
    check_doubles(sizes, n, "sizes");
    check_logicals(in_idx, n, "in_idx");

    cozip_entry_t *entries =
        build_entries(names, R_NilValue, sizes, in_idx, n);

    cozip_error_t err = {0};
    cozip_status_t s  = cozip_plan(entries, (size_t)n, &err);
    if (s != COZIP_OK) R_THROW_COZIP(s, err);

    SEXP off_v = PROTECT(alloc_integer64(n));
    SEXP sz_v  = PROTECT(alloc_integer64(n));
    int64_t *off_p = (int64_t*)REAL(off_v);
    int64_t *sz_p  = (int64_t*)REAL(sz_v);
    for (R_xlen_t i = 0; i < n; i++) {
        off_p[i] = (int64_t)entries[i].payload_offset;
        sz_p[i]  = (int64_t)entries[i].payload_size;
    }

    SEXP res = PROTECT(Rf_allocVector(VECSXP, 2));
    SET_VECTOR_ELT(res, 0, off_v);
    SET_VECTOR_ELT(res, 1, sz_v);

    SEXP nm = PROTECT(Rf_allocVector(STRSXP, 2));
    SET_STRING_ELT(nm, 0, Rf_mkChar("offset"));
    SET_STRING_ELT(nm, 1, Rf_mkChar("size"));
    Rf_setAttrib(res, R_NamesSymbol, nm);

    UNPROTECT(4);
    return res;
}

SEXP R_cozip_finalize(SEXP out_path, SEXP names, SEXP paths,
                      SEXP sizes, SEXP in_idx, SEXP profile) {
    if (TYPEOF(out_path) != STRSXP || Rf_xlength(out_path) != 1
        || STRING_ELT(out_path, 0) == NA_STRING) {
        Rf_error("'out_path' must be a single non-NA string");
    }
    if (TYPEOF(profile) != INTSXP || Rf_xlength(profile) != 1
        || INTEGER(profile)[0] == NA_INTEGER) {
        Rf_error("'profile' must be a single non-NA integer");
    }

    R_xlen_t n = Rf_xlength(names);
    check_chars(names, n, "names");
    check_chars(paths, n, "paths");
    check_doubles(sizes, n, "sizes");
    check_logicals(in_idx, n, "in_idx");

    cozip_entry_t *entries =
        build_entries(names, paths, sizes, in_idx, n);

    /* Every entry must have a path at finalize time. The R-side
     * create() guarantees this (the __metadata__ placeholder gets
     * its real path patched in before this call). */
    for (R_xlen_t i = 0; i < n; i++) {
        if (entries[i].source.kind == COZIP_SOURCE_NONE) {
            Rf_error("Entry %lld ('%s') has no path",
                     (long long)(i + 1), entries[i].arc_name);
        }
    }

    cozip_error_t err = {0};
    cozip_status_t s;
    cozip_profile_t p = (cozip_profile_t)INTEGER(profile)[0];

    /* 1. Plan offsets. */
    s = cozip_plan(entries, (size_t)n, &err);
    if (s != COZIP_OK) R_THROW_COZIP(s, err);

    /* 2. Size the index payload. */
    size_t idx_size = 0;
    s = cozip_index_payload_size(entries, (size_t)n, &idx_size, &err);
    if (s != COZIP_OK) R_THROW_COZIP(s, err);

    /* 3. Build the index payload. */
    uint8_t *payload = (uint8_t*)R_alloc(idx_size, sizeof(uint8_t));
    s = cozip_build_index_payload(entries, (size_t)n, p,
                                  payload, idx_size, &err);
    if (s != COZIP_OK) R_THROW_COZIP(s, err);

    /* 4. Write the archive (libzip). */
    const char *out = Rf_translateCharUTF8(STRING_ELT(out_path, 0));
    s = cozip_write_archive(out, entries, (size_t)n,
                            payload, idx_size, &err);
    if (s != COZIP_OK) R_THROW_COZIP(s, err);

    /* 5. Patch the FNV-1a 64 integrity hash into bytes 43..50. */
    s = cozip_patch_integrity_hash(out, idx_size, &err);
    if (s != COZIP_OK) R_THROW_COZIP(s, err);

    return R_NilValue;
}


/* ---- 3. Routine registration ---- */

static const R_CallMethodDef CallEntries[] = {
    {"R_cozip_plan",     (DL_FUNC) &R_cozip_plan,     3},
    {"R_cozip_finalize", (DL_FUNC) &R_cozip_finalize, 6},
    {NULL, NULL, 0}
};

attribute_visible void R_init_cozip(DllInfo *dll) {
    R_registerRoutines(dll, NULL, CallEntries, NULL, NULL);
    R_useDynamicSymbols(dll, FALSE);
    R_forceSymbols(dll, TRUE);
}