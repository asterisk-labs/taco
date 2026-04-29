// cozip.c — implementation of cozip.h.
//
// Organization (top to bottom):
//   1. Platform shims and includes.
//   2. Little-endian byte readers / writers.
//   3. Error helpers (set_err, status_string).
//   4. Writer-side computation: cozip_plan, index_payload_size,
//      build_index_payload, build_extra_field.
//   5. LFH and index parsing: parse_lfh, index_parse, index_get, index_find.
//      These are the only places that defend against malformed input —
//      every byte read is bounds-checked.
//   6. Hash primitive (fnv1a_64) and integrity verification (verify_hash).
//   7. Disk I/O: patch_integrity_hash, write_archive (libzip-backed).
//
// Philosophy: this layer speaks bytes and memory, not spec. Format-level
// rules (name validation, duplicate detection) are the binding's job. C
// only protects memory and refuses malformed parse input.

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

// Forces fseeko/ftello to be 64-bit on glibc, needed for archives > 4 GiB.
#ifndef _FILE_OFFSET_BITS
#  define _FILE_OFFSET_BITS 64
#endif

#include "cozip.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <zip.h>

#ifndef _MSC_VER
#  include <sys/types.h>
#endif

// Writer-side internals (not part of the public ABI).
#define LFH_BASE_SIZE          30u
#define ZIP64_LFH_EXTRA_SIZE   20u
#define ZIP32_SIZE_THRESHOLD   0xFFFFFFFFu

// Compile-time guards: if anyone changes the spec constants and forgets
// to update a related one, the build breaks here instead of producing
// silently wrong archives.
_Static_assert(COZIP_INDEX_OFFSET == 30 + 9 + 12, "LFH layout");
_Static_assert(COZIP_INDEX_NAME_LEN == sizeof(COZIP_INDEX_NAME) - 1,
               "COZIP_INDEX_NAME_LEN must match COZIP_INDEX_NAME");

// Portable 64-bit seek/tell. POSIX fseeko/ftello on Unix; the _i64
// variants on MSVC. We use long long throughout so off_t (which differs
// in size between platforms) never leaks into the public API.
#if defined(_MSC_VER)
#  define cozip_fseek64(fp, off) _fseeki64((fp), (__int64)(off), SEEK_SET)
#  define cozip_fseek_end(fp)    _fseeki64((fp), 0, SEEK_END)
#  define cozip_ftell64(fp)      ((long long)_ftelli64(fp))
#else
#  define cozip_fseek64(fp, off) fseeko((fp), (off_t)(off), SEEK_SET)
#  define cozip_fseek_end(fp)    fseeko((fp), 0, SEEK_END)
#  define cozip_ftell64(fp)      ((long long)ftello(fp))
#endif


// Little-endian byte readers and writers.
//
// ZIP stores every multi-byte field little-endian on disk regardless of
// host endianness. These helpers read/write one byte at a time so we
// don't depend on the host being LE and don't trip alignment faults on
// strict-alignment architectures (some ARMs).

static inline void put_u16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
}

static inline void put_u32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
  p[2] = (uint8_t)(v >> 16);
  p[3] = (uint8_t)(v >> 24);
}

static inline void put_u64(uint8_t* p, uint64_t v) {
  put_u32(p,     (uint32_t)v);
  put_u32(p + 4, (uint32_t)(v >> 32));
}

static inline uint16_t get_u16(const uint8_t* p) {
  return (uint16_t)(p[0] | (p[1] << 8));
}

static inline uint32_t get_u32(const uint8_t* p) {
  return (uint32_t)p[0]
       | ((uint32_t)p[1] << 8)
       | ((uint32_t)p[2] << 16)
       | ((uint32_t)p[3] << 24);
}

static inline uint64_t get_u64(const uint8_t* p) {
  return (uint64_t)get_u32(p) | ((uint64_t)get_u32(p + 4) << 32);
}


// Error helper. Tolerates err == NULL so callers that don't care about
// diagnostics can pass NULL and still get the status code as the return
// value.

static cozip_status_t set_err(cozip_error_t* err, cozip_status_t code,
                              const char* fmt, ...) {
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

const char* cozip_status_string(cozip_status_t status) {
  switch (status) {
    case COZIP_OK:                       return "OK";
    case COZIP_ERR_INVALID_LFH:          return "INVALID_LFH";
    case COZIP_ERR_INVALID_MAGIC:        return "INVALID_MAGIC";
    case COZIP_ERR_UNSUPPORTED_VERSION:  return "UNSUPPORTED_VERSION";
    case COZIP_ERR_UNKNOWN_PROFILE:      return "UNKNOWN_PROFILE";
    case COZIP_ERR_ARCHIVE_TOO_SMALL:    return "ARCHIVE_TOO_SMALL";
    case COZIP_ERR_TRUNCATED_INDEX:      return "TRUNCATED_INDEX";
    case COZIP_ERR_MISSING_ENTRY:        return "MISSING_ENTRY";
    case COZIP_ERR_INVALID_ARGUMENT:     return "INVALID_ARGUMENT";
    case COZIP_ERR_BUFFER_TOO_SMALL:     return "BUFFER_TOO_SMALL";
    case COZIP_ERR_IO:                   return "IO";
  }
  return "UNKNOWN";
}


// Writer-side computation.
//
// The archive layout is fully determined by:
//   - COZIP_INDEX_OFFSET (constant, 51 bytes for the index LFH)
//   - the size of the index payload (depends on in_index entries)
//   - each entry's LFH size + payload size
//
// cozip_plan walks the entries in order and assigns the byte offset
// where each LFH starts and where each payload starts. It's pure
// arithmetic and infallible — the caller has already verified names
// and rejected duplicates.

// LFH size for a regular entry: 30 bytes fixed + name length + optional
// 20-byte ZIP64 extra when the payload is too large for ZIP32 fields.
static inline uint64_t lfh_size_for(const char* arc_name, uint64_t payload_size) {
  uint64_t base = LFH_BASE_SIZE + (uint64_t)strlen(arc_name);
  return (payload_size >= ZIP32_SIZE_THRESHOLD) ? base + ZIP64_LFH_EXTRA_SIZE
                                                : base;
}

cozip_status_t cozip_plan(cozip_entry_t* entries, size_t n,
                          cozip_error_t* err) {
  (void)err;

  // The index payload sits between byte 51 and the first user LFH.
  // Its size depends only on the names of the in_index entries.
  uint64_t idx_payload_size = COZIP_INDEX_HEADER_SIZE;
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) {
      idx_payload_size += COZIP_INDEX_PER_ENTRY_OVERHEAD
                        + (uint64_t)strlen(entries[i].arc_name);
    }
  }

  // Cursor advances entry by entry: LFH then payload, in order.
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

// Mirror of the in_index sum inside cozip_plan. Both must agree —
// build_index_payload uses this to validate the caller's buffer size.
cozip_status_t cozip_index_payload_size(const cozip_entry_t* entries, size_t n,
                                        size_t* out_size, cozip_error_t* err) {
  (void)err;
  size_t total = COZIP_INDEX_HEADER_SIZE;
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) {
      total += COZIP_INDEX_PER_ENTRY_OVERHEAD + strlen(entries[i].arc_name);
    }
  }
  *out_size = total;
  return COZIP_OK;
}


// Index payload serialization.
//
// The payload is laid out in five regions (columnar, not row-by-row):
//
//   [ header (11 B) ][ name_lens (2 B × n) ][ names ][ offsets (8 B × n) ][ sizes (8 B × n) ]
//
// Columnar instead of interleaved so a reader that only needs offsets
// can skip the names entirely — useful for fast random access by index.

cozip_status_t cozip_build_index_payload(const cozip_entry_t* entries, size_t n,
                                         cozip_profile_t profile,
                                         uint8_t* out, size_t out_size,
                                         cozip_error_t* err) {
  size_t needed;
  cozip_index_payload_size(entries, n, &needed, NULL);
  if (out_size < needed) {
    return set_err(err, COZIP_ERR_BUFFER_TOO_SMALL,
                   "need %zu bytes, got %zu", needed, out_size);
  }

  uint32_t n_indexed = 0;
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) n_indexed++;
  }

  uint8_t* p = out;

  // Header: magic(4) + version(2) + profile(1) + n_entries(4) = 11 bytes.
  memcpy(p, COZIP_MAGIC, COZIP_MAGIC_LEN); p += COZIP_MAGIC_LEN;
  put_u16(p, COZIP_FORMAT_VERSION); p += 2;
  *p++ = (uint8_t)profile;
  put_u32(p, n_indexed); p += 4;

  // Name lengths.
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) {
      put_u16(p, (uint16_t)strlen(entries[i].arc_name));
      p += 2;
    }
  }
  // Names, concatenated, no separators.
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) {
      size_t nl = strlen(entries[i].arc_name);
      memcpy(p, entries[i].arc_name, nl);
      p += nl;
    }
  }
  // Payload offsets.
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) {
      put_u64(p, entries[i].payload_offset);
      p += 8;
    }
  }
  // Payload sizes.
  for (size_t i = 0; i < n; i++) {
    if (entries[i].in_index) {
      put_u64(p, entries[i].payload_size);
      p += 8;
    }
  }
  return COZIP_OK;
}

// 12-byte 0xCA0C extra: header_id(2) + data_size(2) + 8-byte zero hash.
// The hash is filled in by cozip_patch_integrity_hash after the archive
// is written.
void cozip_build_extra_field(uint8_t out[COZIP_EXTRA_FIELD_SIZE]) {
  put_u16(out + 0, COZIP_EXTRA_HEADER_ID);
  put_u16(out + 2, COZIP_EXTRA_DATA_SIZE);
  memset(out + 4, 0, 8);
}


// LFH parsing (defensive: input may be a corrupted or hostile file).
//
// cozip_parse_lfh handles ONLY the first LFH of the archive, which
// carries the __cozip__ name and the 0xCA0C extra. The LFHs of user
// entries are never parsed by cozip — readers go straight to the
// payload via the offsets stored in the index.
//
// Byte layout of the first 51 bytes:
//   0   PK\x03\x04 signature
//   4   version_needed (2)
//   6   gp flags (2)         — bit 11 must be set (UTF-8 names)
//   8   method (2)           — STORE
//   10  dos time/date (4)
//   14  crc32 (4)            — over the index payload
//   18  compressed_size (4)  — index payload size
//   22  uncompressed_size (4)
//   26  filename_len (2)     — must be 9
//   28  extra_len (2)        — must be 12
//   30  "__cozip__"          — 9 bytes
//   39  extra header_id (2)  — must be 0xCA0C
//   41  extra data_size (2)  — must be 8
//   43  hash (8)             — FNV-1a 64

cozip_status_t cozip_parse_lfh(const uint8_t* data, size_t data_size,
                               cozip_lfh_info_t* out, cozip_error_t* err) {
  if (data_size < COZIP_INDEX_OFFSET) {
    return set_err(err, COZIP_ERR_INVALID_LFH,
                   "buffer too short: %zu < %d", data_size, COZIP_INDEX_OFFSET);
  }
  if (data[0] != 'P' || data[1] != 'K' || data[2] != 0x03 || data[3] != 0x04) {
    return set_err(err, COZIP_ERR_INVALID_LFH, "bad ZIP signature");
  }
  if (memcmp(data + 30, COZIP_INDEX_NAME, COZIP_INDEX_NAME_LEN) != 0) {
    return set_err(err, COZIP_ERR_INVALID_LFH, "first entry is not __cozip__");
  }

  uint16_t header_id = get_u16(data + 39);
  uint16_t ds        = get_u16(data + 41);
  if (header_id != COZIP_EXTRA_HEADER_ID) {
    return set_err(err, COZIP_ERR_INVALID_LFH,
                   "missing 0xCA0C extra (got 0x%04X)", header_id);
  }
  if (ds != COZIP_EXTRA_DATA_SIZE) {
    return set_err(err, COZIP_ERR_INVALID_LFH,
                   "bad extra data_size: %u, want %u", ds,
                   COZIP_EXTRA_DATA_SIZE);
  }

  out->index_size = get_u32(data + 18);   // LFH compressed_size
  out->hash       = get_u64(data + 43);   // 8-byte hash inside the extra
  return COZIP_OK;
}


// Index parsing.
//
// The five regions of the index payload (header + name_lens + names +
// offsets + sizes) must each fit inside the provided buffer. We walk
// through them sequentially, validating the bound at each step. The
// invariant after each block: `pos` points at the start of the next
// region, and pos <= payload_size.
//
// We do NOT copy any data — the resulting cozip_index_t holds pointers
// into the caller's payload buffer. The caller must keep that buffer
// alive for as long as the index is used.

cozip_status_t cozip_index_parse(const uint8_t* payload, size_t payload_size,
                                 cozip_index_t* out, cozip_error_t* err) {
  if (payload_size < COZIP_INDEX_HEADER_SIZE) {
    return set_err(err, COZIP_ERR_TRUNCATED_INDEX,
                   "payload too short: %zu", payload_size);
  }
  if (memcmp(payload, COZIP_MAGIC, COZIP_MAGIC_LEN) != 0) {
    return set_err(err, COZIP_ERR_INVALID_MAGIC, "bad magic");
  }

  uint16_t version = get_u16(payload + 4);
  if (version > COZIP_FORMAT_VERSION) {
    return set_err(err, COZIP_ERR_UNSUPPORTED_VERSION,
                   "format version %u not supported", version);
  }
  uint8_t profile = payload[6];
  if (profile != COZIP_PROFILE_NONE && profile != COZIP_PROFILE_FLAT &&
      profile != COZIP_PROFILE_TACO) {
    return set_err(err, COZIP_ERR_UNKNOWN_PROFILE,
                   "profile %u not recognized", profile);
  }
  uint32_t n_entries = get_u32(payload + 7);

  // name_lens region: n_entries × 2 bytes.
  size_t pos = COZIP_INDEX_HEADER_SIZE;
  if (pos + (size_t)n_entries * 2 > payload_size) {
    return set_err(err, COZIP_ERR_TRUNCATED_INDEX, "name_lens out of bounds");
  }
  size_t name_lens_pos = pos;
  pos += (size_t)n_entries * 2;

  // names region: variable length, sized by summing name_lens above.
  size_t names_total = 0;
  for (uint32_t i = 0; i < n_entries; i++) {
    names_total += get_u16(payload + name_lens_pos + 2 * i);
  }
  if (pos + names_total > payload_size) {
    return set_err(err, COZIP_ERR_TRUNCATED_INDEX, "names out of bounds");
  }
  size_t names_pos = pos;
  pos += names_total;

  // offsets region: n_entries × 8 bytes.
  if (pos + (size_t)n_entries * 8 > payload_size) {
    return set_err(err, COZIP_ERR_TRUNCATED_INDEX, "offsets out of bounds");
  }
  size_t offsets_pos = pos;
  pos += (size_t)n_entries * 8;

  // sizes region: n_entries × 8 bytes.
  if (pos + (size_t)n_entries * 8 > payload_size) {
    return set_err(err, COZIP_ERR_TRUNCATED_INDEX, "sizes out of bounds");
  }
  size_t sizes_pos = pos;
  pos += (size_t)n_entries * 8;

  // The five regions must consume the buffer exactly.
  if (pos != payload_size) {
    return set_err(err, COZIP_ERR_TRUNCATED_INDEX,
                   "size mismatch: parsed %zu, buffer has %zu",
                   pos, payload_size);
  }

  out->version       = version;
  out->profile       = (cozip_profile_t)profile;
  out->n_entries     = n_entries;
  out->_payload      = payload;
  out->_payload_size = payload_size;
  // Pointer arithmetic only; reads always go through get_uXX so unaligned
  // hosts (strict-alignment ARMs) stay safe.
  out->_name_lens    = (const uint16_t*)(payload + name_lens_pos);
  out->_names_offset = names_pos;
  out->_offsets      = (const uint64_t*)(payload + offsets_pos);
  out->_sizes        = (const uint64_t*)(payload + sizes_pos);
  return COZIP_OK;
}

// Random-access read of the i-th entry. The name byte offset has to be
// computed by summing name_lens[0..i-1] — this is O(i). For full
// iteration prefer walking i = 0..n_entries-1 and accumulating the
// offset incrementally (which is what cozip_index_find does internally).
cozip_status_t cozip_index_get(const cozip_index_t* index, uint32_t i,
                               cozip_index_entry_t* out, cozip_error_t* err) {
  if (i >= index->n_entries) {
    return set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                   "index %u out of range (%u entries)", i, index->n_entries);
  }
  size_t name_byte_offset = 0;
  for (uint32_t k = 0; k < i; k++) {
    name_byte_offset += get_u16((const uint8_t*)(index->_name_lens + k));
  }
  uint16_t nl = get_u16((const uint8_t*)(index->_name_lens + i));

  out->name     = (const char*)(index->_payload + index->_names_offset
                                + name_byte_offset);
  out->name_len = nl;
  out->offset   = get_u64((const uint8_t*)(index->_offsets + i));
  out->size     = get_u64((const uint8_t*)(index->_sizes + i));
  return COZIP_OK;
}

// Linear scan with incremental name offset — O(n) total instead of the
// O(n²) you'd get if we called cozip_index_get in a loop.
cozip_status_t cozip_index_find(const cozip_index_t* index, const char* name,
                                cozip_index_entry_t* out, cozip_error_t* err) {
  size_t name_len = strlen(name);
  size_t name_byte_offset = 0;
  for (uint32_t i = 0; i < index->n_entries; i++) {
    uint16_t nl = get_u16((const uint8_t*)(index->_name_lens + i));
    if ((size_t)nl == name_len) {
      const char* candidate = (const char*)(index->_payload
                                            + index->_names_offset
                                            + name_byte_offset);
      if (memcmp(candidate, name, name_len) == 0) {
        out->name     = candidate;
        out->name_len = nl;
        out->offset   = get_u64((const uint8_t*)(index->_offsets + i));
        out->size     = get_u64((const uint8_t*)(index->_sizes + i));
        return COZIP_OK;
      }
    }
    name_byte_offset += nl;
  }
  return set_err(err, COZIP_ERR_MISSING_ENTRY, "entry '%s' not found", name);
}


// Hashing.
//
// FNV-1a 64 was picked over xxhash/cityhash so the C core stays
// dependency-free. The hash is for tamper detection, not crypto.
// Streamable: pass the previous return value as `seed` to continue.

uint64_t cozip_fnv1a_64(const uint8_t* data, size_t size, uint64_t seed) {
  uint64_t h = seed;
  for (size_t i = 0; i < size; i++) {
    h ^= (uint64_t)data[i];
    h *= COZIP_FNV_PRIME;  // unsigned wrap is well-defined and matches spec
  }
  return h;
}

// The integrity hash covers two regions: the index payload (which is
// the "structural" data) and the trailing 32 KiB (which contains the
// CD + EOCD on a typical archive). Bounding the suffix at 32 KiB makes
// verification cost independent of archive size.
//
// Edge case: on very small archives the index region might extend into
// the trailing 32 KiB window. We skip the overlap so each byte is
// hashed exactly once.

cozip_status_t cozip_verify_hash(const uint8_t* archive, size_t archive_size,
                                 uint32_t index_size, uint64_t stored_hash,
                                 bool* out_valid, cozip_error_t* err) {
  if (archive_size < COZIP_MIN_ARCHIVE_SIZE) {
    return set_err(err, COZIP_ERR_ARCHIVE_TOO_SMALL,
                   "archive too small: %zu < %d",
                   archive_size, COZIP_MIN_ARCHIVE_SIZE);
  }

  uint64_t h = COZIP_FNV_OFFSET_BASIS;
  h = cozip_fnv1a_64(archive + COZIP_INDEX_OFFSET, index_size, h);

  size_t suffix_start = archive_size - COZIP_HASH_WINDOW_SIZE;
  size_t index_end    = (size_t)COZIP_INDEX_OFFSET + index_size;

  if (index_end <= suffix_start) {
    h = cozip_fnv1a_64(archive + suffix_start, COZIP_HASH_WINDOW_SIZE, h);
  } else {
    // Index region overlaps the trailing window; skip the overlap.
    size_t keep = index_end - suffix_start;
    h = cozip_fnv1a_64(archive + index_end,
                       COZIP_HASH_WINDOW_SIZE - keep, h);
  }
  *out_valid = (h == stored_hash);
  return COZIP_OK;
}


// Hash patching (chunked, no allocation).
//
// cozip_patch_integrity_hash runs after the archive is already on disk:
// recompute the same FNV input that cozip_verify_hash would and write
// the 8-byte result at byte 43 (inside the 0xCA0C extra of the first
// LFH). We hash via 8 KiB read chunks instead of mmap or alloc to keep
// the call's memory footprint flat.

static cozip_status_t hash_range(FILE* fp, long long start, size_t len,
                                 uint64_t* h, cozip_error_t* err) {
  uint8_t buf[8192];
  if (cozip_fseek64(fp, start) != 0) {
    return set_err(err, COZIP_ERR_IO, "seek failed");
  }
  while (len > 0) {
    size_t want = len < sizeof(buf) ? len : sizeof(buf);
    if (fread(buf, 1, want, fp) != want) {
      return set_err(err, COZIP_ERR_IO, "read failed");
    }
    *h = cozip_fnv1a_64(buf, want, *h);
    len -= want;
  }
  return COZIP_OK;
}

cozip_status_t cozip_patch_integrity_hash(const char* archive_path,
                                          size_t index_payload_size,
                                          cozip_error_t* err) {
  FILE* fp = fopen(archive_path, "r+b");
  if (!fp) {
    return set_err(err, COZIP_ERR_IO, "cannot open '%s'", archive_path);
  }

  if (cozip_fseek_end(fp) != 0) {
    fclose(fp);
    return set_err(err, COZIP_ERR_IO, "seek-end failed");
  }
  long long archive_size = cozip_ftell64(fp);
  if (archive_size < (long long)COZIP_MIN_ARCHIVE_SIZE) {
    fclose(fp);
    return set_err(err, COZIP_ERR_ARCHIVE_TOO_SMALL,
                   "archive too small: %lld", archive_size);
  }

  // 1. Hash the index region.
  uint64_t h = COZIP_FNV_OFFSET_BASIS;
  cozip_status_t s = hash_range(fp, COZIP_INDEX_OFFSET, index_payload_size,
                                &h, err);
  if (s != COZIP_OK) { fclose(fp); return s; }

  // 2. Hash the trailing 32 KiB, skipping any overlap with the index.
  long long suffix_start = archive_size - COZIP_HASH_WINDOW_SIZE;
  long long index_end    = (long long)COZIP_INDEX_OFFSET
                         + (long long)index_payload_size;

  if (index_end <= suffix_start) {
    s = hash_range(fp, suffix_start, COZIP_HASH_WINDOW_SIZE, &h, err);
  } else {
    size_t keep = (size_t)(index_end - suffix_start);
    s = hash_range(fp, index_end, COZIP_HASH_WINDOW_SIZE - keep, &h, err);
  }
  if (s != COZIP_OK) { fclose(fp); return s; }

  // 3. Patch the 8-byte hash into bytes 43..50 of the first LFH.
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


// Disk writer (libzip-backed).
//
// Three phases:
//   1. Add the __cozip__ entry first, with the 0xCA0C extra carrying a
//      zero hash placeholder. The probe (probe/libzip_probe.c) confirmed
//      libzip writes this exactly as the cozip spec requires: 51 bytes
//      total, no auto-added UT/Unix2 extras.
//   2. Add user entries in the given order. STORE compression, UTF-8
//      names. libzip drives the actual writing on zip_close.
//   3. Sanity-check the result by re-reading the first 51 bytes and
//      validating with cozip_parse_lfh. If a future libzip version
//      starts adding extras (or anything else drifts), this trips
//      loudly instead of producing a silent bad archive.

cozip_status_t cozip_write_archive(const char* out_path,
                                   const cozip_entry_t* entries, size_t n,
                                   const uint8_t* index_payload,
                                   size_t index_payload_size,
                                   cozip_error_t* err) {
  int zerr = 0;
  zip_t* za = zip_open(out_path, ZIP_CREATE | ZIP_TRUNCATE, &zerr);
  if (!za) {
    return set_err(err, COZIP_ERR_IO, "zip_open failed (libzip err=%d)", zerr);
  }

  // Phase 1: __cozip__ entry.
  zip_source_t* idx_src =
      zip_source_buffer(za, index_payload, index_payload_size, 0);
  if (!idx_src) {
    zip_discard(za);
    return set_err(err, COZIP_ERR_IO, "zip_source_buffer for index failed");
  }
  zip_int64_t idx_id =
      zip_file_add(za, COZIP_INDEX_NAME, idx_src, ZIP_FL_ENC_UTF_8);
  if (idx_id < 0) {
    const char* msg = zip_strerror(za);
    zip_source_free(idx_src);
    zip_discard(za);
    return set_err(err, COZIP_ERR_IO,
                   "zip_file_add(__cozip__): %s", msg ? msg : "");
  }

  // Attach the 0xCA0C extra to the LFH only (not the central directory).
  // The hash starts as 8 zero bytes; cozip_patch_integrity_hash fills it.
  uint8_t zero8[8] = {0};
  if (zip_file_extra_field_set(za, (zip_uint64_t)idx_id,
                               COZIP_EXTRA_HEADER_ID, ZIP_EXTRA_FIELD_NEW,
                               zero8, 8, ZIP_FL_LOCAL) < 0) {
    const char* msg = zip_strerror(za);
    zip_discard(za);
    return set_err(err, COZIP_ERR_IO,
                   "set 0xCA0C extra: %s", msg ? msg : "");
  }
  if (zip_set_file_compression(za, (zip_uint64_t)idx_id,
                               ZIP_CM_STORE, 0) < 0) {
    zip_discard(za);
    return set_err(err, COZIP_ERR_IO, "STORE on __cozip__ failed");
  }

  // Phase 2: user entries, in caller order.
  for (size_t i = 0; i < n; i++) {
    const cozip_entry_t* e = &entries[i];
    zip_source_t* src = NULL;

    if (e->source.kind == COZIP_SOURCE_PATH) {
      // Pass the exact size so libzip skips a stat call.
      src = zip_source_file(za, e->source.u.path, 0,
                            (zip_int64_t)e->payload_size);
    } else if (e->source.kind == COZIP_SOURCE_BUFFER) {
      src = zip_source_buffer(za, e->source.u.buffer.data,
                              e->source.u.buffer.size, 0);
    }

    if (!src) {
      const char* msg = zip_strerror(za);
      zip_discard(za);
      return set_err(err, COZIP_ERR_IO,
                     "source for '%s': %s", e->arc_name, msg ? msg : "");
    }

    zip_int64_t added = zip_file_add(za, e->arc_name, src, ZIP_FL_ENC_UTF_8);
    if (added < 0) {
      const char* msg = zip_strerror(za);
      zip_source_free(src);
      zip_discard(za);
      return set_err(err, COZIP_ERR_IO,
                     "zip_file_add('%s'): %s", e->arc_name, msg ? msg : "");
    }
    if (zip_set_file_compression(za, (zip_uint64_t)added,
                                 ZIP_CM_STORE, 0) < 0) {
      zip_discard(za);
      return set_err(err, COZIP_ERR_IO,
                     "STORE on '%s' failed", e->arc_name);
    }
  }

  if (zip_close(za) < 0) {
    return set_err(err, COZIP_ERR_IO, "zip_close failed");
  }

  // Phase 3: post-write sanity check.
  FILE* fp = fopen(out_path, "rb");
  if (!fp) {
    return set_err(err, COZIP_ERR_IO, "post-write open failed");
  }
  uint8_t lfh[COZIP_INDEX_OFFSET];
  size_t got = fread(lfh, 1, sizeof(lfh), fp);
  fclose(fp);
  if (got != sizeof(lfh)) {
    return set_err(err, COZIP_ERR_IO, "post-write short read");
  }
  cozip_lfh_info_t info;
  return cozip_parse_lfh(lfh, sizeof(lfh), &info, err);
}