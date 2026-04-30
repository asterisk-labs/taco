/*
 * cozip 1.0 writer C ABI, implementation.
 *
 * This file implements the public API declared in cozip.h. The C
 * core only writes archives. Reading and parsing live in the
 * language bindings, in DuckDB, or in any host that already
 * understands ZIP byte ranges and Parquet metadata.
 *
 * The implementation is split into the following sections.
 *
 *   1. Platform shims and includes.
 *   2. Internal constants and build-time guards.
 *   3. Portable 64-bit seek and tell.
 *   4. Little-endian byte readers and writers.
 *   5. Error helpers.
 *   6. FNV-1a 64 (internal).
 *   7. Writer-side computation (cozip_plan, payload sizing,
 *      payload serialization, extra field).
 *   8. Disk I/O (hash patching, archive writing, post-write LFH
 *      validation).
 */


/* ---- 1. Platform shims and includes ---- */

#if defined(__linux__) || defined(__gnu_linux__)
#  ifndef _GNU_SOURCE
#    define _GNU_SOURCE
#  endif
#elif defined(__APPLE__) || defined(__MACH__)
#  ifndef _POSIX_C_SOURCE
#    define _POSIX_C_SOURCE 200809L
#  endif
#elif defined(_WIN32) || defined(_WIN64)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#endif

/* Forces fseeko/ftello to be 64-bit on glibc, needed for archives
 * larger than 4 GiB.
 */
#ifndef _FILE_OFFSET_BITS
#  define _FILE_OFFSET_BITS 64
#endif

#include "cozip.h"
#include "version.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <zip.h>

#ifndef _MSC_VER
#  include <sys/types.h>
#endif


/* ---- 2. Internal constants and build-time guards ---- */

#define LFH_BASE_SIZE          30u
#define ZIP64_LFH_EXTRA_SIZE   20u
#define ZIP32_SIZE_THRESHOLD   0xFFFFFFFFu

/* Read buffer for cozip_patch_integrity_hash. Sized to read the
 * entire 32 KiB suffix region in one fread call.
 */
#define HASH_BUF_SIZE          32768u

/* General-purpose flag bit masks used by validate_index_lfh and
 * the post-write LFH validation in cozip_write_archive. Mirrors
 * APPNOTE 6.3.10 section 4.4.4. Bits 0, 3, 6 and 13 must be
 * clear; bit 11 is informational because all cozip-written names
 * pass through libzip with ZIP_FL_ENC_UTF_8.
 */
#define GP_ENCRYPTED           (1u << 0)
#define GP_DATA_DESCRIPTOR     (1u << 3)
#define GP_STRONG_ENCRYPTION   (1u << 6)
#define GP_UTF8                (1u << 11)
#define GP_CD_ENCRYPTED        (1u << 13)
#define GP_FORBIDDEN_MASK      (GP_ENCRYPTED | GP_DATA_DESCRIPTOR | \
                                GP_STRONG_ENCRYPTION | GP_CD_ENCRYPTED)

_Static_assert(COZIP_INDEX_OFFSET == 30 + 9 + 12,
               "LFH layout, index payload must start at byte 51");
_Static_assert(COZIP_INDEX_NAME_LEN == sizeof(COZIP_INDEX_NAME) - 1,
               "COZIP_INDEX_NAME_LEN must match COZIP_INDEX_NAME");
_Static_assert(HASH_BUF_SIZE >= COZIP_HASH_WINDOW_SIZE,
               "hash buffer must hold the full 32 KiB suffix region");


/* ---- 3. Portable 64-bit seek and tell ---- */

#if defined(_MSC_VER)
#  define cozip_fseek64(fp, off) _fseeki64((fp), (__int64)(off), SEEK_SET)
#  define cozip_fseek_end(fp)    _fseeki64((fp), 0, SEEK_END)
#  define cozip_ftell64(fp)      ((long long)_ftelli64(fp))
#else
#  define cozip_fseek64(fp, off) fseeko((fp), (off_t)(off), SEEK_SET)
#  define cozip_fseek_end(fp)    fseeko((fp), 0, SEEK_END)
#  define cozip_ftell64(fp)      ((long long)ftello(fp))
#endif

/* ---- 3. Version string ---- */

COZIP_API const char* cozip_version_string(void) {
    return COZIP_VERSION_STRING;
}

/* ---- 4. Little-endian byte readers and writers ---- */

/* ZIP stores every multi-byte field little-endian on disk. These
 * helpers read and write one byte at a time so the implementation
 * does not depend on the host being little-endian and stays safe
 * on strict-alignment hosts (some ARM cores).
 */

static inline void put_u16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static inline void put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static inline void put_u64(uint8_t *p, uint64_t v) {
    put_u32(p,     (uint32_t)v);
    put_u32(p + 4, (uint32_t)(v >> 32));
}

static inline uint16_t get_u16(const uint8_t *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

static inline uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static inline uint64_t get_u64(const uint8_t *p) {
    return (uint64_t)get_u32(p) | ((uint64_t)get_u32(p + 4) << 32);
}


/* ---- 5. Error helpers ---- */

/* Tolerates `err == NULL` so callers that do not care about
 * diagnostics can pass NULL and still get the status code as the
 * return value.
 */
static cozip_status_t set_err(cozip_error_t *err, cozip_status_t code,
                              const char *fmt, ...) {
    if (err) {
        err->code = code;
        if (fmt) {
            va_list ap;
            va_start(ap, fmt);
            vsnprintf(err->message, COZIP_ERROR_MESSAGE_SIZE, fmt, ap);
            va_end(ap);
        } else {
            err->message[0] = '\0';
        }
    }
    return code;
}

const char *cozip_status_string(cozip_status_t status) {
    switch (status) {
        case COZIP_OK:                    return "OK";
        case COZIP_ERR_INVALID_LFH:       return "INVALID_LFH";
        case COZIP_ERR_ARCHIVE_TOO_SMALL: return "ARCHIVE_TOO_SMALL";
        case COZIP_ERR_INVALID_ARGUMENT:  return "INVALID_ARGUMENT";
        case COZIP_ERR_BUFFER_TOO_SMALL:  return "BUFFER_TOO_SMALL";
        case COZIP_ERR_IO:                return "IO";
    }
    return "UNKNOWN";
}


/* ---- 6. FNV-1a 64 (internal) ---- */

/* Seed with COZIP_FNV_OFFSET_BASIS for a fresh hash, or with a
 * previous return value to continue across non-contiguous chunks.
 * The hash is order-sensitive; bytes must be presented in
 * archive-byte order.
 */
static uint64_t fnv1a_64(const uint8_t *data, size_t size, uint64_t seed) {
    uint64_t h = seed;
    for (size_t i = 0; i < size; i++) {
        h ^= (uint64_t)data[i];
        h *= COZIP_FNV_PRIME;
    }
    return h;
}


/* ---- 7. Writer-side computation ---- */

/* Byte size of a single LFH for an entry with the given name and
 * payload size. Adds a 20-byte ZIP64 extra when the payload size
 * meets or exceeds the ZIP32 sentinel value, mirroring what libzip
 * does for large entries.
 */
static inline uint64_t lfh_size_for(const char *arc_name,
                                    uint64_t payload_size) {
    uint64_t base = LFH_BASE_SIZE + (uint64_t)strlen(arc_name);
    return (payload_size >= ZIP32_SIZE_THRESHOLD) ? base + ZIP64_LFH_EXTRA_SIZE
                                                  : base;
}

cozip_status_t cozip_plan(cozip_entry_t *entries, size_t n,
                          cozip_error_t *err) {
    (void)err;

    /* The index payload sits between byte 51 and the first user LFH.
     * Its size is fully determined by the names of the in_index
     * entries.
     */
    uint64_t idx_payload_size = COZIP_INDEX_HEADER_SIZE;
    for (size_t i = 0; i < n; i++) {
        if (entries[i].in_index) {
            idx_payload_size += COZIP_INDEX_PER_ENTRY_OVERHEAD
                              + (uint64_t)strlen(entries[i].arc_name);
        }
    }

    uint64_t cursor = (uint64_t)COZIP_INDEX_OFFSET + idx_payload_size;
    for (size_t i = 0; i < n; i++) {
        entries[i].lfh_offset     = cursor;
        entries[i].lfh_size       = lfh_size_for(entries[i].arc_name,
                                                 entries[i].payload_size);
        entries[i].payload_offset = cursor + entries[i].lfh_size;
        cursor = entries[i].payload_offset + entries[i].payload_size;
    }
    return COZIP_OK;
}

cozip_status_t cozip_index_payload_size(const cozip_entry_t *entries, size_t n,
                                        size_t *out_size,
                                        cozip_error_t *err) {
    uint64_t total = COZIP_INDEX_HEADER_SIZE;
    for (size_t i = 0; i < n; i++) {
        if (!entries[i].in_index) continue;
        size_t name_len = strlen(entries[i].arc_name);
        if (name_len > UINT16_MAX) {
            return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                           "arc_name longer than UINT16_MAX on entry %zu "
                           "(%zu bytes)", i, name_len);
        }
        total += COZIP_INDEX_PER_ENTRY_OVERHEAD + (uint64_t)name_len;
    }

    /* The __cozip__ entry is ZIP32 by spec (5.2.1). Its compressed
     * size field is u32, so the index payload cannot reach the
     * 0xFFFFFFFF sentinel.
     */
    if (total >= ZIP32_SIZE_THRESHOLD) {
        return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                       "index payload would exceed ZIP32 limit "
                       "(%llu bytes)", (unsigned long long)total);
    }

    *out_size = (size_t)total;
    return COZIP_OK;
}

cozip_status_t cozip_build_index_payload(const cozip_entry_t *entries, size_t n,
                                         cozip_profile_t profile,
                                         uint8_t *out, size_t out_size,
                                         cozip_error_t *err) {
    size_t needed = 0;
    cozip_status_t s = cozip_index_payload_size(entries, n, &needed, err);
    if (s != COZIP_OK) return s;

    if (out_size < needed) {
        return set_err(err, COZIP_ERR_BUFFER_TOO_SMALL,
                       "need %zu bytes, got %zu", needed, out_size);
    }

    uint32_t n_indexed = 0;
    for (size_t i = 0; i < n; i++) {
        if (entries[i].in_index) n_indexed++;
    }

    uint8_t *p = out;

    /* Header */
    memcpy(p, COZIP_MAGIC, COZIP_MAGIC_LEN);
    p += COZIP_MAGIC_LEN;
    put_u16(p, COZIP_FORMAT_VERSION); p += 2;
    *p++ = (uint8_t)profile;
    put_u32(p, n_indexed); p += 4;

    /* Name lengths */
    for (size_t i = 0; i < n; i++) {
        if (!entries[i].in_index) continue;
        put_u16(p, (uint16_t)strlen(entries[i].arc_name));
        p += 2;
    }
    /* Names, concatenated, no separators */
    for (size_t i = 0; i < n; i++) {
        if (!entries[i].in_index) continue;
        size_t nl = strlen(entries[i].arc_name);
        memcpy(p, entries[i].arc_name, nl);
        p += nl;
    }
    /* Payload offsets */
    for (size_t i = 0; i < n; i++) {
        if (!entries[i].in_index) continue;
        put_u64(p, entries[i].payload_offset);
        p += 8;
    }
    /* Payload sizes */
    for (size_t i = 0; i < n; i++) {
        if (!entries[i].in_index) continue;
        put_u64(p, entries[i].payload_size);
        p += 8;
    }
    return COZIP_OK;
}

void cozip_build_extra_field(uint8_t out[COZIP_EXTRA_FIELD_SIZE]) {
    put_u16(out + 0, COZIP_EXTRA_HEADER_ID);
    put_u16(out + 2, COZIP_EXTRA_DATA_SIZE);
    memset(out + 4, 0, 8);
}


/* ---- 8. Disk I/O ---- */

/* Streams `len` bytes starting at archive offset `start` through
 * the FNV-1a 64 hasher, accumulating into *h. Reads in
 * HASH_BUF_SIZE (32 KiB) chunks so the trailing suffix region is
 * read in one fread call.
 */
static cozip_status_t hash_range(FILE *fp, long long start, size_t len,
                                 uint64_t *h, cozip_error_t *err) {
    uint8_t buf[HASH_BUF_SIZE];

    if (cozip_fseek64(fp, start) != 0) {
        return set_err(err, COZIP_ERR_IO, "seek failed");
    }
    while (len > 0) {
        size_t want = len < sizeof(buf) ? len : sizeof(buf);
        if (fread(buf, 1, want, fp) != want) {
            return set_err(err, COZIP_ERR_IO, "read failed");
        }
        *h = fnv1a_64(buf, want, *h);
        len -= want;
    }
    return COZIP_OK;
}

cozip_status_t cozip_patch_integrity_hash(const char *archive_path,
                                          size_t index_payload_size,
                                          cozip_error_t *err) {
    FILE *fp = fopen(archive_path, "r+b");
    if (!fp) {
        return set_err(err, COZIP_ERR_IO,
                       "cannot open '%s'", archive_path);
    }

    if (cozip_fseek_end(fp) != 0) {
        fclose(fp);
        return set_err(err, COZIP_ERR_IO, "seek-end failed");
    }
    long long archive_size = cozip_ftell64(fp);
    if (archive_size < (long long)COZIP_MIN_ARCHIVE_SIZE) {
        fclose(fp);
        return set_err(err, COZIP_ERR_ARCHIVE_TOO_SMALL,
                       "archive too small (%lld bytes)", archive_size);
    }

    /* Reject inconsistent index_payload_size before any read */
    if (index_payload_size == 0 ||
        index_payload_size >
            (size_t)archive_size - (size_t)COZIP_INDEX_OFFSET) {
        fclose(fp);
        return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                       "index_payload_size out of range (%zu)",
                       index_payload_size);
    }

    /* 1. Hash the index region */
    uint64_t h = COZIP_FNV_OFFSET_BASIS;
    cozip_status_t s = hash_range(fp, COZIP_INDEX_OFFSET,
                                  index_payload_size, &h, err);
    if (s != COZIP_OK) { fclose(fp); return s; }

    /* 2. Hash the trailing 32 KiB, skipping any overlap with the
     * index region.
     */
    long long suffix_start = archive_size - COZIP_HASH_WINDOW_SIZE;
    long long index_end    = (long long)COZIP_INDEX_OFFSET
                           + (long long)index_payload_size;

    if (index_end <= suffix_start) {
        s = hash_range(fp, suffix_start, COZIP_HASH_WINDOW_SIZE, &h, err);
    } else {
        size_t keep = (size_t)(index_end - suffix_start);
        s = hash_range(fp, index_end,
                       COZIP_HASH_WINDOW_SIZE - keep, &h, err);
    }
    if (s != COZIP_OK) { fclose(fp); return s; }

    /* 3. Patch the 8-byte hash into bytes 43..50 of the first LFH */
    if (cozip_fseek64(fp, 43) != 0) {
        fclose(fp);
        return set_err(err, COZIP_ERR_IO, "seek to hash slot failed");
    }
    uint8_t hb[8];
    put_u64(hb, h);
    if (fwrite(hb, 1, 8, fp) != 8) {
        fclose(fp);
        return set_err(err, COZIP_ERR_IO, "writing hash failed");
    }

    fclose(fp);
    return COZIP_OK;
}

/* Validates the first 51 bytes of an archive against the cozip
 * 1.0 specification, section 8.5 step 1, and confirms that the
 * compressed size in the LFH equals `expected_index_size`.
 *
 * Layout of the first 51 bytes,
 *
 *    0   PK\x03\x04                    signature
 *    4   version_needed (2)
 *    6   gp flags (2)                  bit 11 informational, 0/3/6/13 clear
 *    8   method (2)                    must be 0 (STORE)
 *   10   dos time/date (4)
 *   14   crc32 (4)
 *   18   compressed_size (4)           must equal expected_index_size
 *   22   uncompressed_size (4)         must equal compressed_size
 *   26   filename_len (2)              must be 9
 *   28   extra_len (2)                 must be 12
 *   30   "__cozip__"                   9 bytes
 *   39   extra header_id (2)           must be 0xCA0C
 *   41   extra data_size (2)           must be 8
 *   43   integrity hash (8)            FNV-1a 64
 */
static cozip_status_t validate_index_lfh(const uint8_t *data,
                                         uint32_t expected_index_size,
                                         cozip_error_t *err) {
    /* Signature */
    if (data[0] != 'P' || data[1] != 'K' ||
        data[2] != 0x03 || data[3] != 0x04) {
        return set_err(err, COZIP_ERR_INVALID_LFH, "bad ZIP signature");
    }

    /* General-purpose flags. Bit 11 is informational because
     * "__cozip__" is pure ASCII; the forbidden flags are still
     * rejected.
     */
    uint16_t flags = get_u16(data + 6);
    if (flags & GP_FORBIDDEN_MASK) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "forbidden GP flag set (0x%04X)", flags);
    }

    /* Compression method must be STORE */
    uint16_t method = get_u16(data + 8);
    if (method != 0) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "non-STORE compression method (%u)", method);
    }

    /* Sizes must be equal, non-zero, and not the ZIP64 sentinel */
    uint32_t csz = get_u32(data + 18);
    uint32_t usz = get_u32(data + 22);
    if (csz == 0 || csz == ZIP32_SIZE_THRESHOLD || csz != usz) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "bad index sizes (compressed=%u, uncompressed=%u)",
                       csz, usz);
    }

    /* Filename and extra geometry */
    uint16_t fnlen = get_u16(data + 26);
    uint16_t exlen = get_u16(data + 28);
    if (fnlen != COZIP_INDEX_NAME_LEN) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "wrong filename length (%u, want %u)",
                       fnlen, COZIP_INDEX_NAME_LEN);
    }
    if (exlen != COZIP_EXTRA_FIELD_SIZE) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "wrong extra field length (%u, want %u)",
                       exlen, COZIP_EXTRA_FIELD_SIZE);
    }

    /* Filename literal */
    if (memcmp(data + 30, COZIP_INDEX_NAME, COZIP_INDEX_NAME_LEN) != 0) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "first entry is not __cozip__");
    }

    /* 0xCA0C extra field shape */
    uint16_t header_id = get_u16(data + 39);
    uint16_t ds        = get_u16(data + 41);
    if (header_id != COZIP_EXTRA_HEADER_ID) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "missing 0xCA0C extra (got 0x%04X)", header_id);
    }
    if (ds != COZIP_EXTRA_DATA_SIZE) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "wrong extra data_size (%u, want %u)",
                       ds, COZIP_EXTRA_DATA_SIZE);
    }

    /* Cross-check the written index size against the planned size */
    if (csz != expected_index_size) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "written index size differs from planned size "
                       "(got %u, want %u)",
                       csz, expected_index_size);
    }

    return COZIP_OK;
}

/* Reads the fixed-size LFH head of one priority entry as written by
 * libzip and validates it against the offsets cozip_plan computed.
 * Confirms ZIP signature, GP flags, STORE method, payload size,
 * filename length, extra length, and that the resulting payload
 * offset matches `e->payload_offset`.
 *
 * Only the first 30 bytes of the LFH are read. The filename body
 * itself is not re-validated, which is fine because the binding
 * already verified names before cozip_plan ran.
 */
static cozip_status_t verify_priority_lfh(FILE *fp, const cozip_entry_t *e,
                                          cozip_error_t *err) {
    uint8_t lfh[LFH_BASE_SIZE];

    if (cozip_fseek64(fp, (long long)e->lfh_offset) != 0) {
        return set_err(err, COZIP_ERR_IO,
                       "seek to LFH for '%s' failed", e->arc_name);
    }
    if (fread(lfh, 1, sizeof(lfh), fp) != sizeof(lfh)) {
        return set_err(err, COZIP_ERR_IO,
                       "short read on LFH for '%s'", e->arc_name);
    }

    if (lfh[0] != 'P' || lfh[1] != 'K' ||
        lfh[2] != 0x03 || lfh[3] != 0x04) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "bad ZIP signature for '%s'", e->arc_name);
    }

    uint16_t flags = get_u16(lfh + 6);
    if (flags & GP_FORBIDDEN_MASK) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "forbidden GP flag on '%s' (0x%04X)",
                       e->arc_name, flags);
    }
    /* Bit 11 (UTF-8) is required only when the filename has bytes
     * >= 0x80. ASCII-only names are unambiguously valid UTF-8
     * either way, and several libzip builds omit the flag for
     * them, so a strict check would reject correct archives.
     */
    bool needs_utf8_flag = false;
    size_t arc_len = strlen(e->arc_name);
    for (size_t i = 0; i < arc_len; i++) {
        if ((unsigned char)e->arc_name[i] >= 0x80) {
            needs_utf8_flag = true;
            break;
        }
    }
    if (needs_utf8_flag && (flags & GP_UTF8) == 0) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "UTF-8 flag required on '%s' (non-ASCII name)",
                       e->arc_name);
    }

    uint16_t method = get_u16(lfh + 8);
    if (method != 0) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "non-STORE method on '%s' (%u)",
                       e->arc_name, method);
    }

    uint16_t fnlen = get_u16(lfh + 26);
    uint16_t exlen = get_u16(lfh + 28);

    size_t expected_fnlen = arc_len;
    if ((size_t)fnlen != expected_fnlen) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "filename length mismatch on '%s' "
                       "(got %u, want %zu)",
                       e->arc_name, fnlen, expected_fnlen);
    }

    uint32_t csz = get_u32(lfh + 18);
    uint32_t usz = get_u32(lfh + 22);

    /* Sizes and extra geometry depend on whether the entry is ZIP64 */
    if (e->payload_size >= ZIP32_SIZE_THRESHOLD) {
        if (csz != ZIP32_SIZE_THRESHOLD || usz != ZIP32_SIZE_THRESHOLD) {
            return set_err(err, COZIP_ERR_INVALID_LFH,
                           "missing ZIP64 sentinel on '%s'", e->arc_name);
        }
        if (exlen != ZIP64_LFH_EXTRA_SIZE) {
            return set_err(err, COZIP_ERR_INVALID_LFH,
                           "wrong extra length on ZIP64 entry '%s' "
                           "(got %u, want %u)",
                           e->arc_name, exlen, ZIP64_LFH_EXTRA_SIZE);
        }
    } else {
        if (csz != usz) {
            return set_err(err, COZIP_ERR_INVALID_LFH,
                           "size mismatch on '%s' "
                           "(compressed=%u, uncompressed=%u)",
                           e->arc_name, csz, usz);
        }
        if ((uint64_t)csz != e->payload_size) {
            return set_err(err, COZIP_ERR_INVALID_LFH,
                           "wrong size on '%s' (got %u, want %llu)",
                           e->arc_name, csz,
                           (unsigned long long)e->payload_size);
        }
        if (exlen != 0) {
            return set_err(err, COZIP_ERR_INVALID_LFH,
                           "unexpected extra field on '%s' (%u bytes)",
                           e->arc_name, exlen);
        }
    }

    /* The actual payload offset must match what cozip_plan computed */
    uint64_t actual_payload_offset = e->lfh_offset
                                   + LFH_BASE_SIZE
                                   + (uint64_t)fnlen
                                   + (uint64_t)exlen;
    if (actual_payload_offset != e->payload_offset) {
        return set_err(err, COZIP_ERR_INVALID_LFH,
                       "payload offset mismatch on '%s' "
                       "(got %llu, planned %llu)",
                       e->arc_name,
                       (unsigned long long)actual_payload_offset,
                       (unsigned long long)e->payload_offset);
    }

    return COZIP_OK;
}

cozip_status_t cozip_write_archive(const char *out_path,
                                   const cozip_entry_t *entries, size_t n,
                                   const uint8_t *index_payload,
                                   size_t index_payload_size,
                                   cozip_error_t *err) {
    /* Pre-flight checks on the index payload size */
    if (index_payload_size == 0 ||
        index_payload_size >= ZIP32_SIZE_THRESHOLD) {
        return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                       "index_payload_size out of range (%zu)",
                       index_payload_size);
    }

    /* Pre-flight checks on entry sources */
    for (size_t i = 0; i < n; i++) {
        const cozip_entry_t *e = &entries[i];
        if (e->source.kind == COZIP_SOURCE_NONE) {
            return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                           "entry '%s' has no source", e->arc_name);
        }
        if (e->source.kind == COZIP_SOURCE_BUFFER &&
            e->source.u.buffer.size != e->payload_size) {
            return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                           "buffer size differs from payload_size on '%s' "
                           "(buffer=%zu, payload=%llu)",
                           e->arc_name, e->source.u.buffer.size,
                           (unsigned long long)e->payload_size);
        }
    }

    int zerr = 0;
    zip_t *za = zip_open(out_path, ZIP_CREATE | ZIP_TRUNCATE, &zerr);
    if (!za) {
        return set_err(err, COZIP_ERR_IO,
                       "zip_open failed (libzip err=%d)", zerr);
    }

    /* Phase 1, the __cozip__ index entry */
    zip_source_t *idx_src =
        zip_source_buffer(za, index_payload, index_payload_size, 0);
    if (!idx_src) {
        zip_discard(za);
        return set_err(err, COZIP_ERR_IO,
                       "zip_source_buffer for index failed");
    }
    zip_int64_t idx_id =
        zip_file_add(za, COZIP_INDEX_NAME, idx_src, ZIP_FL_ENC_UTF_8);
    if (idx_id < 0) {
        const char *msg = zip_strerror(za);
        zip_source_free(idx_src);
        zip_discard(za);
        return set_err(err, COZIP_ERR_IO,
                       "zip_file_add(__cozip__) failed (%s)",
                       msg ? msg : "");
    }

    /* The 0xCA0C extra is attached to the LFH only. The hash starts
     * as 8 zero bytes and is patched by cozip_patch_integrity_hash.
     */
    uint8_t zero8[8] = {0};
    if (zip_file_extra_field_set(za, (zip_uint64_t)idx_id,
                                 COZIP_EXTRA_HEADER_ID, ZIP_EXTRA_FIELD_NEW,
                                 zero8, 8, ZIP_FL_LOCAL) < 0) {
        const char *msg = zip_strerror(za);
        zip_discard(za);
        return set_err(err, COZIP_ERR_IO,
                       "set 0xCA0C extra failed (%s)", msg ? msg : "");
    }
    if (zip_set_file_compression(za, (zip_uint64_t)idx_id,
                                 ZIP_CM_STORE, 0) < 0) {
        zip_discard(za);
        return set_err(err, COZIP_ERR_IO,
                       "STORE on __cozip__ failed");
    }

    /* Phase 2, user entries in caller order */
    for (size_t i = 0; i < n; i++) {
        const cozip_entry_t *e = &entries[i];
        zip_source_t *src = NULL;

        if (e->source.kind == COZIP_SOURCE_PATH) {
            src = zip_source_file(za, e->source.u.path, 0,
                                  (zip_int64_t)e->payload_size);
        } else if (e->source.kind == COZIP_SOURCE_BUFFER) {
            src = zip_source_buffer(za, e->source.u.buffer.data,
                                    e->source.u.buffer.size, 0);
        }

        if (!src) {
            const char *msg = zip_strerror(za);
            zip_discard(za);
            return set_err(err, COZIP_ERR_IO,
                           "source for '%s' failed (%s)",
                           e->arc_name, msg ? msg : "");
        }

        zip_int64_t added = zip_file_add(za, e->arc_name, src,
                                         ZIP_FL_ENC_UTF_8);
        if (added < 0) {
            const char *msg = zip_strerror(za);
            zip_source_free(src);
            zip_discard(za);
            return set_err(err, COZIP_ERR_IO,
                           "zip_file_add('%s') failed (%s)",
                           e->arc_name, msg ? msg : "");
        }
        if (zip_set_file_compression(za, (zip_uint64_t)added,
                                     ZIP_CM_STORE, 0) < 0) {
            zip_discard(za);
            return set_err(err, COZIP_ERR_IO,
                           "STORE on '%s' failed", e->arc_name);
        }
    }

    /* Phase 3, finalize the archive. libzip docs require zip_discard
     * after zip_close failure to free the archive struct.
     */
    if (zip_close(za) < 0) {
        const char *msg = zip_strerror(za);
        cozip_status_t s = set_err(err, COZIP_ERR_IO,
                                   "zip_close failed (%s)",
                                   msg ? msg : "");
        zip_discard(za);
        return s;
    }

    /* Phase 4, post-write validation. Re-open the archive read-only
     * and verify (a) the __cozip__ LFH conforms to spec section 8.5
     * step 1 and its written size equals what we passed in, and (b)
     * every priority entry's LFH places its payload exactly at the
     * offset cozip_plan computed.
     */
    FILE *fp = fopen(out_path, "rb");
    if (!fp) {
        return set_err(err, COZIP_ERR_IO, "post-write open failed");
    }

    uint8_t lfh[COZIP_INDEX_OFFSET];
    if (fread(lfh, 1, sizeof(lfh), fp) != sizeof(lfh)) {
        fclose(fp);
        return set_err(err, COZIP_ERR_IO, "post-write short read");
    }

    cozip_status_t s = validate_index_lfh(lfh, (uint32_t)index_payload_size,
                                          err);
    if (s != COZIP_OK) {
        fclose(fp);
        return s;
    }

    for (size_t i = 0; i < n; i++) {
        if (!entries[i].in_index) continue;
        s = verify_priority_lfh(fp, &entries[i], err);
        if (s != COZIP_OK) {
            fclose(fp);
            return s;
        }
    }

    fclose(fp);
    return COZIP_OK;
}