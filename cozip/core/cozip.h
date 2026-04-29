/*
 * cozip 1.0 core C ABI
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


/* Versioning.
 *
 * COZIP_VERSION is the human-readable release date of this build.
 * COZIP_VERSION_NUMBER is the same date as a YYYYMMDD integer for
 * compile-time #if checks. COZIP_FORMAT_VERSION is the on-disk
 * version recorded in the index payload header and is independent
 * of the library version.
 */
#define COZIP_VERSION         "2026.04.28"
#define COZIP_VERSION_NUMBER  20260428
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
 * The four ASCII bytes at offset 0 of the index payload that
 * cozip_index_parse uses to recognize a cozip index.
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
 * Used by cozip_fnv1a_64 to build the integrity hash of an
 * archive.
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
 * Codes 1..99 are format errors raised when an archive or buffer
 * does not match the cozip specification (a malformed Local File
 * Header, an unknown profile byte, a truncated index payload).
 *
 * Codes 100 and above are runtime errors raised by the call
 * itself (an out-of-range entry index, an undersized output
 * buffer, an inconsistent argument, a failed read or write).
 */
typedef enum cozip_status {
    COZIP_OK = 0,

    COZIP_ERR_INVALID_LFH         = 1,
    COZIP_ERR_INVALID_MAGIC       = 2,
    COZIP_ERR_UNSUPPORTED_VERSION = 3,
    COZIP_ERR_UNKNOWN_PROFILE     = 4,
    COZIP_ERR_ARCHIVE_TOO_SMALL   = 5,
    COZIP_ERR_TRUNCATED_INDEX     = 6,
    COZIP_ERR_MISSING_ENTRY       = 7,

    COZIP_ERR_INVALID_ARGUMENT    = 100,
    COZIP_ERR_BUFFER_TOO_SMALL    = 101,
    COZIP_ERR_IO                  = 102
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
const char *cozip_status_string(cozip_status_t status);


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
 * Used at write time only. Readers ignore this field.
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
cozip_status_t cozip_plan(cozip_entry_t *entries, size_t n,
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
cozip_status_t cozip_index_payload_size(const cozip_entry_t *entries, size_t n,
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
cozip_status_t cozip_build_index_payload(const cozip_entry_t *entries, size_t n,
                                         cozip_profile_t profile,
                                         uint8_t *out, size_t out_size,
                                         cozip_error_t *err);

/* Writes the 12 bytes of the 0xCA0C extra field with the
 * integrity hash zeroed.
 *
 * Layout is header_id(2), data_size(2) and 8 zero bytes for the
 * hash. The hash is filled in by cozip_patch_integrity_hash
 * after the archive is fully written.
 */
void cozip_build_extra_field(uint8_t out[COZIP_EXTRA_FIELD_SIZE]);


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
 * Phase 4 re-parses the first 51 bytes through cozip_parse_lfh,
 * checks that the written index size equals
 * `index_payload_size`, and reads the fixed LFH head of every
 * priority entry to confirm signature, GP flags, STORE method,
 * sizes, filename and extra lengths, and that the resulting
 * payload offset matches the value cozip_plan computed. If
 * anything disagrees, returns COZIP_ERR_INVALID_LFH and the
 * archive must be considered invalid.
 *
 * `index_payload` and `index_payload_size` describe the index
 * payload bytes as produced by cozip_build_index_payload. The
 * payload is copied into the archive by libzip.
 *
 * The integrity hash is left zero. Call
 * cozip_patch_integrity_hash next to compute and patch it.
 */
cozip_status_t cozip_write_archive(const char *out_path,
                                   const cozip_entry_t *entries, size_t n,
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
cozip_status_t cozip_patch_integrity_hash(const char *archive_path,
                                          size_t index_payload_size,
                                          cozip_error_t *err);


/* Result of parsing the first 51 bytes of a cozip archive.
 *
 * `index_size` is the value of the LFH compressed_size field
 * for the index entry, which equals the byte length of the
 * index payload. `hash` is the 8-byte FNV-1a 64 value carried
 * in the 0xCA0C extra field at archive bytes 43..50.
 */
typedef struct cozip_lfh_info {
    uint32_t index_size;
    uint64_t hash;
} cozip_lfh_info_t;

/* Validates the first 51 bytes of an archive against the cozip
 * 1.0 specification, section 8.5 step 1, and extracts the index
 * size and stored integrity hash.
 *
 * `data_size` must be at least COZIP_INDEX_OFFSET. Anything
 * beyond byte 51 is ignored.
 *
 * Confirms the ZIP local file header signature, the GP flag
 * bits (bits 0/3/6/13 clear; bit 11 is not enforced since
 * the __cozip__ filename is pure ASCII), STORE compression
 * method, non-zero matching compressed and uncompressed sizes
 * that are not the ZIP64 sentinel, filename length 9, extra
 * length 12, the literal "__cozip__" filename and the 0xCA0C
 * extra field shape (header_id and data_size).
 *
 * Does not validate the index payload itself; that is the job
 * of cozip_index_parse.
 *
 * Returns COZIP_ERR_INVALID_LFH if any check fails.
 */
cozip_status_t cozip_parse_lfh(const uint8_t *data, size_t data_size,
                               cozip_lfh_info_t *out, cozip_error_t *err);


/* One entry in a parsed cozip index.
 *
 * `name` is a non-owning pointer into the index payload buffer
 * passed to cozip_index_parse. It is NOT null-terminated; use
 * `name_len` for its length. `offset` and `size` are the
 * entry's payload offset and byte length, both measured against
 * byte 0 of the archive.
 */
typedef struct cozip_index_entry {
    const char *name;
    uint16_t    name_len;
    uint64_t    offset;
    uint64_t    size;
} cozip_index_entry_t;

/* A parsed cozip index, holding non-owning views into the
 * payload buffer passed to cozip_index_parse.
 *
 * The payload buffer must outlive every read of this struct.
 * As soon as the buffer is freed or reused, every pointer in
 * this struct dangles.
 *
 * `version`, `profile` and `n_entries` come straight from the
 * 11-byte payload header. The remaining underscore-prefixed
 * fields are private and used by cozip_index_get and
 * cozip_index_find. Do not read them directly; use the
 * accessors.
 */
typedef struct cozip_index {
    uint16_t        version;
    cozip_profile_t profile;
    uint32_t        n_entries;

    const uint8_t *_payload;
    size_t         _payload_size;
    const uint8_t *_name_lens;
    const uint8_t *_names;
    const uint8_t *_offsets;
    const uint8_t *_sizes;
} cozip_index_t;

/* Parses the index payload into a non-owning view.
 *
 * `payload` must point to the index payload bytes, starting at
 * the 4-byte "CZIP" magic. `payload_size` must equal the index
 * size reported by cozip_parse_lfh.
 *
 * The resulting cozip_index_t holds pointers into `payload`.
 * The caller must keep the buffer alive for as long as the
 * index is used.
 *
 * Validates the binary geometry of the payload only. Concretely,
 * checks the header magic, the format version, the profile byte,
 * the bounds of all five payload regions, that every name length
 * is greater than zero, that the sum of name lengths does not
 * overflow size_t, and that every payload size is greater than
 * zero.
 *
 * Does not check names against the __cozip__ reservation, UTF-8
 * well-formedness, name uniqueness, or whether payload offsets
 * fit within the actual archive size. Higher layers, typically
 * the language binding, own those checks.
 *
 * Returns COZIP_ERR_INVALID_MAGIC, COZIP_ERR_UNSUPPORTED_VERSION,
 * COZIP_ERR_UNKNOWN_PROFILE or COZIP_ERR_TRUNCATED_INDEX.
 */
cozip_status_t cozip_index_parse(const uint8_t *payload, size_t payload_size,
                                 cozip_index_t *out, cozip_error_t *err);

/* Reads the i-th entry of a parsed index.
 *
 * The names region is variable-length, so locating entry `i`
 * requires summing the first `i` name lengths. Each call is
 * therefore O(i). For full iteration, walking i = 0..n_entries-1
 * with a running name offset is faster than calling
 * cozip_index_get in a loop, which would be O(n^2) total.
 *
 * Returns COZIP_ERR_INVALID_ARGUMENT if `i` is out of range.
 */
cozip_status_t cozip_index_get(const cozip_index_t *index, uint32_t i,
                               cozip_index_entry_t *out, cozip_error_t *err);

/* Looks up an entry by exact name match.
 *
 * Linear scan with an incremental name offset, O(n_entries)
 * total. Names are compared as byte strings; no normalization
 * is applied.
 *
 * Returns COZIP_ERR_MISSING_ENTRY if no entry has the given
 * name.
 */
cozip_status_t cozip_index_find(const cozip_index_t *index, const char *name,
                                cozip_index_entry_t *out, cozip_error_t *err);


/* FNV-1a 64.
 *
 * Pass COZIP_FNV_OFFSET_BASIS as `seed` to start a fresh hash.
 * Pass the previous return value to continue across
 * non-contiguous chunks. The hash is order-sensitive; bytes
 * must be presented in archive-byte order.
 */
uint64_t cozip_fnv1a_64(const uint8_t *data, size_t size, uint64_t seed);

/* Recomputes the integrity hash over an in-memory archive and
 * compares it against `stored_hash`.
 *
 * The hash input is the index region (bytes COZIP_INDEX_OFFSET
 * to COZIP_INDEX_OFFSET + index_size) concatenated with the
 * trailing COZIP_HASH_WINDOW_SIZE bytes. Overlapping bytes
 * between the two regions are hashed exactly once.
 *
 * `archive` must hold the entire archive in memory and
 * `archive_size` must be at least COZIP_MIN_ARCHIVE_SIZE.
 * `index_size` is bounds-checked against the archive before any
 * read; an `index_size` of zero, or one that would extend past
 * the end of the archive, is rejected.
 *
 * On success, *out_valid is true if the recomputed hash matches
 * `stored_hash` and false otherwise. Returns
 * COZIP_ERR_ARCHIVE_TOO_SMALL if `archive_size` is below the
 * minimum, or COZIP_ERR_TRUNCATED_INDEX if `index_size` is out
 * of range.
 */
cozip_status_t cozip_verify_hash(const uint8_t *archive, size_t archive_size,
                                 uint32_t index_size, uint64_t stored_hash,
                                 bool *out_valid, cozip_error_t *err);

#ifdef __cplusplus
}
#endif

#endif