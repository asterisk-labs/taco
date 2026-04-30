/*
 * cozip 1.0 writer C ABI
 *
 * Cloud-Optimized ZIP. The first archive entry is a binary index
 * placed at byte 0 that lets a reader locate any priority file in
 * one range request without scanning the Central Directory.
 *
 * This header exposes the byte-and-memory layer of cozip. It owns
 * offset arithmetic, payload serialization, libzip-driven writes,
 * LFH and index parsing, and FNV-1a 64 integrity hashing. Format
 * rules that live above the wire format (UTF-8 validation,
 * duplicate detection, profile-specific reserved names) are the
 * responsibility of the language bindings.
 *
 * Strings are null-terminated UTF-8. The library performs no
 * allocation; callers own every buffer. Functions return
 * cozip_status_t and populate *err on failure (err may be NULL).
 * On non-OK, output parameters are unspecified.
 *
 * All functions are reentrant and thread-safe.
 *
 * See the cozip 1.0 specification for the on-disk format.
 */

#ifndef COZIP_H_
#define COZIP_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif


/* Symbol visibility.
 *
 * COZIP_API marks every function that is part of the public ABI.
 */
#if defined(_WIN32) || defined(__CYGWIN__)
  #ifdef COZIP_BUILDING
    #define COZIP_API __declspec(dllexport)
  #else
    #define COZIP_API __declspec(dllimport)
  #endif
  #define COZIP_LOCAL
#elif defined(__GNUC__) || defined(__clang__)
  #define COZIP_API   __attribute__((visibility("default")))
  #define COZIP_LOCAL __attribute__((visibility("hidden")))
#else
  #define COZIP_API
  #define COZIP_LOCAL
#endif


/* Versioning.
 *
 * COZIP_VERSION is the human-readable release date of this build.
 * COZIP_VERSION_NUMBER is the same date as a YYYYMMDD integer for
 * compile-time #if checks. COZIP_FORMAT_VERSION is the on-disk
 * version recorded in the index payload header and is independent
 * of the library version.
 */
#define COZIP_VERSION         "2026.04.29"
#define COZIP_VERSION_NUMBER  20260429
#define COZIP_FORMAT_VERSION  1


/* Archive layout constants.
 *
 * The index payload starts at byte COZIP_INDEX_OFFSET (51), which
 * is the fixed sum of the 30-byte Local File Header, the 9-byte
 * literal name "__cozip__" and the 12-byte 0xCA0C extra field.
 *
 * The integrity hash covers the index region plus the trailing
 * COZIP_HASH_WINDOW_SIZE bytes of the archive. An archive must
 * therefore be at least COZIP_MIN_ARCHIVE_SIZE bytes long; smaller
 * archives cannot carry both an index and a 32 KiB suffix region.
 */
#define COZIP_INDEX_OFFSET      51
#define COZIP_HASH_WINDOW_SIZE  32768
#define COZIP_MIN_ARCHIVE_SIZE  (COZIP_HASH_WINDOW_SIZE + COZIP_INDEX_OFFSET)


/* Index entry filename.
 *
 * The first ZIP entry of every cozip archive uses this exact
 * 9-byte literal as its filename. Reserved by the spec; no other
 * entry may use it.
 */
#define COZIP_INDEX_NAME      "__cozip__"
#define COZIP_INDEX_NAME_LEN  9


/* Index payload magic.
 *
 * The four ASCII bytes at offset 0 of the index payload, written
 * by cozip_build_index_payload and used by readers to recognize
 * a cozip index.
 */
#define COZIP_MAGIC      "CZIP"
#define COZIP_MAGIC_LEN  4


/* Integrity extra field.
 *
 * 0xCA0C is the cozip extra field carried inside the Local File
 * Header of the index entry. Its 12 bytes are header_id(2),
 * data_size(2) and integrity_hash(8). Only the hash is mutable,
 * and only by cozip_patch_integrity_hash.
 */
#define COZIP_EXTRA_HEADER_ID   0xCA0Cu
#define COZIP_EXTRA_DATA_SIZE   8u
#define COZIP_EXTRA_FIELD_SIZE  12


/* Index payload sizing.
 *
 * COZIP_INDEX_HEADER_SIZE is the 11-byte fixed header at the start
 * of the payload (magic 4, version 2, profile 1, n_entries 4).
 *
 * COZIP_INDEX_PER_ENTRY_OVERHEAD is the per-entry contribution to
 * the payload size, summed across the fixed-width regions of the
 * payload (name_len 2, offset 8, size 8). The variable-length
 * name itself is added on top.
 */
#define COZIP_INDEX_HEADER_SIZE         11
#define COZIP_INDEX_PER_ENTRY_OVERHEAD  18


/* FNV-1a 64 constants.
 *
 * Offset basis and prime as published by Fowler, Noll and Vo.
 * Used internally by cozip_patch_integrity_hash. Exposed so a
 * non-C reader (binding, DuckDB extension, validator) can
 * reproduce the integrity hash without copying magic numbers.
 */
#define COZIP_FNV_OFFSET_BASIS  UINT64_C(0xCBF29CE484222325)
#define COZIP_FNV_PRIME         UINT64_C(0x100000001B3)


/* Maximum length of an error message string, including the
 * terminating null byte.
 */
#define COZIP_ERROR_MESSAGE_SIZE  192


/* Status codes.
 *
 * Every public function returns one of these. COZIP_OK (0) is
 * success.
 *
 */
typedef enum cozip_status {
    COZIP_OK = 0,

    COZIP_ERR_INVALID_LFH       = 1,
    COZIP_ERR_ARCHIVE_TOO_SMALL = 2,

    COZIP_ERR_INVALID_ARGUMENT  = 100,
    COZIP_ERR_BUFFER_TOO_SMALL  = 101,
    COZIP_ERR_IO                = 102
} cozip_status_t;

/* A status code paired with a human-readable description.
 *
 * `message` is a null-terminated UTF-8 string of at most
 * COZIP_ERROR_MESSAGE_SIZE - 1 visible bytes. When a function
 * returns COZIP_OK the contents of `message` are unspecified.
 */
typedef struct cozip_error {
    cozip_status_t code;
    char           message[COZIP_ERROR_MESSAGE_SIZE];
} cozip_error_t;

/* Returns a short, stable English name for a status code, for
 * example "INVALID_LFH" for COZIP_ERR_INVALID_LFH. The pointer
 * has static storage and is never NULL.
 */
COZIP_API const char *cozip_status_string(cozip_status_t status);


/* Profile selector for cozip_build_index_payload.
 *
 * The chosen profile value is written into the third byte of the
 * index payload header and tells a reader which set of priority
 * files to expect.
 *
 * COZIP_PROFILE_NONE makes no profile-level guarantees. The index
 * may list any priority files the writer chose.
 *
 * COZIP_PROFILE_FLAT requires a single priority entry named
 * "__metadata__", a Parquet manifest with at least name, offset
 * and size columns covering every other archive entry.
 *
 * COZIP_PROFILE_TACO requires "COLLECTION.json" plus every
 * Parquet file under "METADATA/" to appear in the index,
 * contiguously placed before the Central Directory.
 *
 * The C core does not enforce profile-level requirements.
 * Bindings validate them.
 */
typedef enum cozip_profile {
    COZIP_PROFILE_NONE = 0,
    COZIP_PROFILE_FLAT = 1,
    COZIP_PROFILE_TACO = 2
} cozip_profile_t;


/* Where cozip_write_archive should pull an entry's payload from.
 *
 * COZIP_SOURCE_NONE is the default zeroed value. Passing it to
 * the writer is rejected with COZIP_ERR_INVALID_ARGUMENT.
 *
 * COZIP_SOURCE_PATH reads the payload from a file on disk.
 *
 * COZIP_SOURCE_BUFFER takes the payload from a memory buffer
 * supplied by the caller. The buffer must outlive the call to
 * cozip_write_archive, and `buffer.size` must equal
 * `payload_size` for the entry.
 */
typedef enum cozip_source_kind {
    COZIP_SOURCE_NONE   = 0,
    COZIP_SOURCE_PATH   = 1,
    COZIP_SOURCE_BUFFER = 2
} cozip_source_kind_t;

/* Tagged union of input sources for a write.
 *
 * `path` is a null-terminated UTF-8 path. The caller owns it.
 * `buffer.data` and `buffer.size` describe a contiguous region
 * of memory holding the entry's payload. The caller owns the
 * buffer.
 */
typedef struct cozip_source {
    cozip_source_kind_t kind;
    union {
        const char *path;
        struct {
            const uint8_t *data;
            size_t         size;
        } buffer;
    } u;
} cozip_source_t;


/* Description of a single ZIP entry, used both as input and as
 * scratch space by the writer pipeline.
 *
 * The caller fills `arc_name`, `payload_size`, `in_index` and
 * `source` before calling cozip_plan. cozip_plan fills
 * `lfh_offset`, `lfh_size` and `payload_offset` in place.
 * cozip_build_index_payload reads `arc_name`, `payload_size`,
 * `in_index` and `payload_offset`. cozip_write_archive consumes
 * `source` and, after libzip closes the archive, re-reads each
 * priority entry's LFH to confirm that the on-disk offsets
 * match `lfh_offset` and `payload_offset`.
 *
 * `arc_name` is the entry's path inside the archive, encoded as
 * null-terminated UTF-8. The caller owns the string and must
 * keep it alive for the duration of the writer pipeline. The
 * byte length of `arc_name` must not exceed UINT16_MAX.
 *
 * `payload_size` is the exact byte length of the entry's
 * payload. It must match the underlying file or buffer size.
 *
 * `in_index` selects whether the entry is listed in the cozip
 * index payload. Entries with in_index = false still appear as
 * regular ZIP entries and remain reachable through the Central
 * Directory.
 */
typedef struct cozip_entry {
    const char     *arc_name;
    uint64_t        payload_size;
    bool            in_index;
    cozip_source_t  source;

    uint64_t lfh_offset;
    uint64_t lfh_size;
    uint64_t payload_offset;
} cozip_entry_t;


/* Computes the on-disk byte layout of a planned archive.
 *
 * Walks `entries` in order and assigns `lfh_offset`, `lfh_size`
 * and `payload_offset` to each one. The first entry's LFH starts
 * at COZIP_INDEX_OFFSET plus the index payload size. Subsequent
 * entries pack tightly with no padding between them.
 *
 * Entries whose `payload_size` exceeds 0xFFFFFFFF receive a
 * 20-byte ZIP64 Local File Header extra, which is reflected in
 * `lfh_size`.
 *
 * Pure arithmetic. No I/O, no allocation, no validation. Always
 * returns COZIP_OK.
 */
COZIP_API cozip_status_t cozip_plan(cozip_entry_t *entries, size_t n,
                                    cozip_error_t *err);

/* Reports the exact byte size of the index payload that
 * cozip_build_index_payload will produce for the same arguments.
 * Use the result to size the output buffer.
 *
 * Sums COZIP_INDEX_HEADER_SIZE plus, for every entry with
 * `in_index` = true, COZIP_INDEX_PER_ENTRY_OVERHEAD plus the
 * byte length of `arc_name`.
 *
 * Returns COZIP_ERR_INVALID_ARGUMENT if any indexed `arc_name`
 * exceeds UINT16_MAX bytes, or if the resulting payload would
 * meet or exceed the ZIP32 size limit (0xFFFFFFFF). The index
 * entry is always ZIP32 by spec section 5.2.1, so the index
 * payload itself cannot use ZIP64.
 */
COZIP_API cozip_status_t cozip_index_payload_size(const cozip_entry_t *entries,
                                                  size_t n,
                                                  size_t *out_size,
                                                  cozip_error_t *err);

/* Serializes the index payload into `out`.
 *
 * The output is a contiguous, columnar binary blob with five
 * regions in order, header followed by name lengths, names,
 * offsets and sizes. Only entries with `in_index` = true are
 * written. The relative order of indexed entries in the payload
 * matches their order in `entries`.
 *
 * `profile` is recorded in the third byte of the header. The C
 * core writes the value verbatim and does not validate that the
 * priority files match the profile's requirements.
 *
 * Internally calls cozip_index_payload_size to compute the
 * required size, so the same COZIP_ERR_INVALID_ARGUMENT
 * conditions apply (oversized name, payload above the ZIP32
 * limit). Returns COZIP_ERR_BUFFER_TOO_SMALL if `out_size` is
 * below the required size.
 */
COZIP_API cozip_status_t cozip_build_index_payload(const cozip_entry_t *entries,
                                                   size_t n,
                                                   cozip_profile_t profile,
                                                   uint8_t *out,
                                                   size_t out_size,
                                                   cozip_error_t *err);

/* Writes the 12 bytes of the 0xCA0C extra field with the
 * integrity hash zeroed.
 *
 * Layout is header_id(2), data_size(2) and 8 zero bytes for the
 * hash. The hash is filled in by cozip_patch_integrity_hash
 * after the archive is fully written.
 */
COZIP_API void cozip_build_extra_field(uint8_t out[COZIP_EXTRA_FIELD_SIZE]);


/* Writes the planned archive to `out_path` via libzip.
 *
 * Phase 1 adds the "__cozip__" entry first, with the 0xCA0C
 * extra carrying a zero-filled hash placeholder. Phase 2 adds
 * the user entries in the order given by the `entries` array.
 * Every entry uses STORE compression and UTF-8 names. Phase 3
 * closes the archive through libzip. Phase 4 reopens the file
 * read-only and validates that what libzip actually wrote
 * matches the plan.
 *
 * Pre-flight checks reject any entry whose `source.kind` is
 * COZIP_SOURCE_NONE, and any COZIP_SOURCE_BUFFER entry whose
 * buffer size disagrees with `payload_size`. Both surface as
 * COZIP_ERR_INVALID_ARGUMENT.
 *
 * Phase 4 re-validates the first 51 bytes (the __cozip__ Local
 * File Header) against the spec, checks that the written index
 * size equals `index_payload_size`, and reads the fixed LFH
 * head of every priority entry to confirm signature, GP flags,
 * STORE method, sizes, filename and extra lengths, and that the
 * resulting payload offset matches the value cozip_plan
 * computed. If anything disagrees, returns
 * COZIP_ERR_INVALID_LFH and the archive must be considered
 * invalid.
 *
 * `index_payload` and `index_payload_size` describe the index
 * payload bytes as produced by cozip_build_index_payload. The
 * payload is copied into the archive by libzip.
 *
 * The integrity hash is left zero. Call
 * cozip_patch_integrity_hash next to compute and patch it.
 */
COZIP_API cozip_status_t cozip_write_archive(const char *out_path,
                                             const cozip_entry_t *entries,
                                             size_t n,
                                             const uint8_t *index_payload,
                                             size_t index_payload_size,
                                             cozip_error_t *err);

/* Computes the FNV-1a 64 hash of an archive and patches it into
 * bytes 43..50 of the file.
 *
 * The hash input is the index region (bytes COZIP_INDEX_OFFSET
 * to COZIP_INDEX_OFFSET + index_payload_size) concatenated with
 * the trailing COZIP_HASH_WINDOW_SIZE bytes of the file. If the
 * two regions overlap on a small archive, overlapping bytes are
 * hashed exactly once.
 *
 * The archive is opened in r+b mode. No bytes outside 43..50
 * are modified.
 *
 * Returns COZIP_ERR_ARCHIVE_TOO_SMALL if the file is shorter
 * than COZIP_MIN_ARCHIVE_SIZE, COZIP_ERR_INVALID_ARGUMENT if
 * `index_payload_size` is zero or larger than the archive can
 * carry, and COZIP_ERR_IO on any read, write or seek failure.
 */
COZIP_API cozip_status_t cozip_patch_integrity_hash(const char *archive_path,
                                                    size_t index_payload_size,
                                                    cozip_error_t *err);

#ifdef __cplusplus
}
#endif

#endif