// cozip — Cloud-Optimized ZIP format.
//
// C ABI for cozip's core. The C layer speaks bytes and memory: it
// computes offsets, serializes the index payload, drives the libzip
// writer, parses headers, and verifies hashes. Format-level rules
// (name validation, duplicate detection, profile-specific reserved
// names, etc.) live in the language bindings — by the time the
// caller reaches this layer, those checks have already happened.
//
// Conventions:
//   - Strings are null-terminated UTF-8.
//   - Caller owns all memory; the library performs no allocation.
//   - Functions return cozip_status_t; on non-OK, *err is populated
//     and output parameters are unspecified. err may be NULL.
//   - All functions are reentrant and thread-safe.

#ifndef COZIP_H_
#define COZIP_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Calendar versioning. Bump on every release.
#define COZIP_VERSION         "2026.04.28"
#define COZIP_VERSION_NUMBER  20260428    // YYYYMMDD, for #if checks
#define COZIP_FORMAT_VERSION  1           // on-disk format, in the index header

// Index payload starts at byte 51:
//   LFH(30) + name "__cozip__"(9) + 0xCA0C extra(12).
#define COZIP_INDEX_OFFSET     51

// Trailing window included in the integrity hash. Fixes the cost of
// verification regardless of archive size.
#define COZIP_HASH_WINDOW_SIZE 32768

// Anything smaller can't carry a valid index plus the hash window.
#define COZIP_MIN_ARCHIVE_SIZE (COZIP_HASH_WINDOW_SIZE + COZIP_INDEX_OFFSET)

#define COZIP_INDEX_NAME       "__cozip__"
#define COZIP_INDEX_NAME_LEN   9

// 4-byte ASCII magic at the start of the index payload.
#define COZIP_MAGIC            "CZIP"
#define COZIP_MAGIC_LEN        4

// 0xCA0C extra field carries the FNV-1a 64 hash. 12 bytes total:
// header_id(2) + data_size(2) + hash(8).
#define COZIP_EXTRA_HEADER_ID  0xCA0Cu
#define COZIP_EXTRA_DATA_SIZE  8u
#define COZIP_EXTRA_FIELD_SIZE 12

// Index payload header: magic(4) + version(2) + profile(1) + n_entries(4).
#define COZIP_INDEX_HEADER_SIZE        11
// Per indexed entry: name_len(2) + offset(8) + size(8).
#define COZIP_INDEX_PER_ENTRY_OVERHEAD 18

// FNV-1a 64. Picked over xxhash/cityhash for zero-dep simplicity; the
// hash is for tamper detection, not crypto.
#define COZIP_FNV_OFFSET_BASIS UINT64_C(0xCBF29CE484222325)
#define COZIP_FNV_PRIME        UINT64_C(0x100000001B3)

#define COZIP_ERROR_MESSAGE_SIZE 192


// Status codes are split in two ranges: format-level problems (1..99)
// surface when parsing or verifying an archive; runtime problems (100+)
// surface from the call itself (memory, I/O).
typedef enum cozip_status {
  COZIP_OK = 0,

  COZIP_ERR_INVALID_LFH         = 1,   // first 51 bytes are not a cozip LFH
  COZIP_ERR_INVALID_MAGIC       = 2,   // index payload doesn't start with "CZIP"
  COZIP_ERR_UNSUPPORTED_VERSION = 3,   // format version newer than this build
  COZIP_ERR_UNKNOWN_PROFILE     = 4,   // profile byte not in {0, 1, 2}
  COZIP_ERR_ARCHIVE_TOO_SMALL   = 5,   // < COZIP_MIN_ARCHIVE_SIZE
  COZIP_ERR_TRUNCATED_INDEX     = 6,   // index payload shorter than declared
  COZIP_ERR_MISSING_ENTRY       = 7,   // cozip_index_find: name not present

  COZIP_ERR_INVALID_ARGUMENT    = 100, // out-of-range index, etc.
  COZIP_ERR_BUFFER_TOO_SMALL    = 101, // caller's output buffer is too small
  COZIP_ERR_IO                  = 102, // open/read/write/seek failed
} cozip_status_t;

typedef struct cozip_error {
  cozip_status_t code;
  char           message[COZIP_ERROR_MESSAGE_SIZE];
} cozip_error_t;

// Short, stable English name for the status code (e.g. "INVALID_LFH").
// The returned pointer has static storage; never NULL.
const char* cozip_status_string(cozip_status_t status);


// Cozip profiles select which "priority files" go into the index. The
// reader uses the profile byte to know how to interpret them:
//   NONE — bare archive, no priority block.
//   FLAT — single __metadata__ Parquet at the end.
//   TACO — COLLECTION.json + METADATA/*.parquet, contiguous before CD.
typedef enum cozip_profile {
  COZIP_PROFILE_NONE = 0,
  COZIP_PROFILE_FLAT = 1,
  COZIP_PROFILE_TACO = 2,
} cozip_profile_t;


// Where the writer should pull each entry's payload from. Used only at
// write time; readers leave entries set to NONE.
typedef enum cozip_source_kind {
  COZIP_SOURCE_NONE   = 0,
  COZIP_SOURCE_PATH   = 1,
  COZIP_SOURCE_BUFFER = 2,
} cozip_source_kind_t;

typedef struct cozip_source {
  cozip_source_kind_t kind;
  union {
    const char* path;        // null-terminated, caller owns
    struct {
      const uint8_t* data;   // caller owns; must outlive the write
      size_t         size;
    } buffer;
  } u;
} cozip_source_t;


// One archive entry. Caller fills the input fields; cozip_plan fills
// the output fields; cozip_write_archive consumes the source. The
// reader uses cozip_index_t / cozip_index_entry_t instead.
typedef struct cozip_entry {
  const char*    arc_name;
  uint64_t       payload_size;
  bool           in_index;          // listed in the index payload?
  cozip_source_t source;

  uint64_t lfh_offset;              // set by cozip_plan
  uint64_t lfh_size;
  uint64_t payload_offset;          // value the index will record for this entry
} cozip_entry_t;


// Computes lfh_offset, lfh_size, and payload_offset for every entry in
// place. Pure arithmetic — no I/O, no name validation, no duplicate
// detection. The caller has already verified those.
cozip_status_t cozip_plan(cozip_entry_t* entries, size_t n,
                          cozip_error_t* err);

// Returns the exact size cozip_build_index_payload will produce. Use
// this to size the output buffer.
cozip_status_t cozip_index_payload_size(const cozip_entry_t* entries, size_t n,
                                        size_t* out_size, cozip_error_t* err);

// Serializes the index payload into out[0..out_size). Only entries
// with in_index = true are listed. Returns BUFFER_TOO_SMALL if
// out_size is below the size reported by cozip_index_payload_size.
cozip_status_t cozip_build_index_payload(const cozip_entry_t* entries, size_t n,
                                         cozip_profile_t profile,
                                         uint8_t* out, size_t out_size,
                                         cozip_error_t* err);

// Writes the 12-byte 0xCA0C extra field with the hash zeroed. The
// hash is patched in later by cozip_patch_integrity_hash.
void cozip_build_extra_field(uint8_t out[COZIP_EXTRA_FIELD_SIZE]);


// Writes the planned archive to disk via libzip. The __cozip__ entry
// is added first with the 0xCA0C extra; then each entry from entries[]
// in the given order. Each entry's source must be PATH or BUFFER. The
// integrity hash is left zero — call cozip_patch_integrity_hash next.
cozip_status_t cozip_write_archive(const char* out_path,
                                   const cozip_entry_t* entries, size_t n,
                                   const uint8_t* index_payload,
                                   size_t index_payload_size,
                                   cozip_error_t* err);

// Computes FNV-1a 64 over (index_region | trailing 32 KiB) and patches
// it into bytes 43..50 of the archive.
cozip_status_t cozip_patch_integrity_hash(const char* archive_path,
                                          size_t index_payload_size,
                                          cozip_error_t* err);


// Parsed first 51 bytes of an archive.
typedef struct cozip_lfh_info {
  uint32_t index_size;   // from the LFH compressed_size field
  uint64_t hash;         // from the 0xCA0C extra
} cozip_lfh_info_t;

// Validates the first 51 bytes and extracts the index size and stored
// hash. data_size must be at least COZIP_INDEX_OFFSET.
cozip_status_t cozip_parse_lfh(const uint8_t* data, size_t data_size,
                               cozip_lfh_info_t* out, cozip_error_t* err);


// One entry in a parsed cozip index. `name` points into the payload
// buffer passed to cozip_index_parse and is NOT null-terminated.
typedef struct cozip_index_entry {
  const char* name;
  uint16_t    name_len;
  uint64_t    offset;
  uint64_t    size;
} cozip_index_entry_t;

// Parsed cozip index. Holds non-owning views into the payload buffer
// passed to cozip_index_parse — that buffer must outlive every read
// of this struct. Underscore fields are private; use the accessors
// (cozip_index_get / cozip_index_find).
typedef struct cozip_index {
  uint16_t        version;
  cozip_profile_t profile;
  uint32_t        n_entries;

  const uint8_t*  _payload;
  size_t          _payload_size;
  const uint16_t* _name_lens;
  size_t          _names_offset;
  const uint64_t* _offsets;
  const uint64_t* _sizes;
} cozip_index_t;

// Parses the index payload into a non-owning view. The payload buffer
// must outlive the resulting cozip_index_t.
cozip_status_t cozip_index_parse(const uint8_t* payload, size_t payload_size,
                                 cozip_index_t* out, cozip_error_t* err);

// Reads the i-th entry. O(i) per call because the names blob has
// variable-length entries — for full iteration prefer walking
// i = 0..n_entries-1 sequentially.
cozip_status_t cozip_index_get(const cozip_index_t* index, uint32_t i,
                               cozip_index_entry_t* out, cozip_error_t* err);

// Looks up an entry by exact name match. Linear in n_entries. Returns
// COZIP_ERR_MISSING_ENTRY if no entry has that name.
cozip_status_t cozip_index_find(const cozip_index_t* index, const char* name,
                                cozip_index_entry_t* out, cozip_error_t* err);


// FNV-1a 64. Seed with COZIP_FNV_OFFSET_BASIS for a fresh hash, or
// with a prior return value to continue across chunks.
uint64_t cozip_fnv1a_64(const uint8_t* data, size_t size, uint64_t seed);

// Recomputes the integrity hash over (index_region | trailing 32 KiB)
// and writes the comparison result to *out_valid. archive must hold
// the entire archive (>= COZIP_MIN_ARCHIVE_SIZE).
cozip_status_t cozip_verify_hash(const uint8_t* archive, size_t archive_size,
                                 uint32_t index_size, uint64_t stored_hash,
                                 bool* out_valid, cozip_error_t* err);

#ifdef __cplusplus
}
#endif

#endif