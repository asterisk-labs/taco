<p align="center">
  <img src="images/asterisk_logo.svg" alt="Asterisk Labs" height="56"/>
  &nbsp;&nbsp;&nbsp;
  <img src="images/cozip_logo.png" alt="cozip" height="56"/>
</p>

# Cloud-Optimized ZIP Format Specification

**Version** 1.0-draft.2  
**Binary format version** 1  
**Status** Draft  
**Date** 2026-04-27  
**License** CC BY 4.0

---

## Part I. Core format

## 1. Introduction

A **cozip** file (Cloud-Optimized ZIP) is a ZIP archive built for random access over byte-addressable storage. It targets workloads where a dataset contains many files and a consumer needs to reach any one of them quickly, in arbitrary order, without downloading the full archive. Machine learning training is the canonical example: a dataloader samples items in random order across an epoch, and every cold lookup must be fast.

A standard ZIP archive places its table of contents, the Central Directory, at the end of the file. A reader must first find and read that table before locating any entry. On archives with millions of files the Central Directory can be very large, and every cold reader pays this cost. A cozip moves the fast access path to the start of the file.

A compact binary index sits at byte 0 and lists the offsets and sizes of the files the creator chose to prioritize. A cozip-aware reader fetches that index and then jumps directly to any priority file. The Central Directory remains present and valid, so standard ZIP tools can still open the archive, but cozip-aware readers do not need the Central Directory for priority-file access.

In practice, priority files often include metadata containers such as Parquet. These files carry typed, queryable descriptions of the archive's data entries. Tools such as DuckDB, Polars, PyArrow, and GeoParquet-compatible readers can then query metadata and construct byte-range pointers without a bespoke archive reader.

A cozip is a finished artefact. After the cozip writer has completed archive construction, profile metadata generation, Central Directory emission, and final integrity-hash patching, the archive **MUST NOT** be modified.

Part I defines the on-disk format, the semantics of the index, and the recommended procedure for remote reading. Part II defines optional **profiles** that fix the names and roles of priority files for specific use cases.

## 2. Conformance

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, **MAY**, and **OPTIONAL** in this specification are to be interpreted as described in RFC 2119 and RFC 8174.

Unless explicitly marked informative, all requirements in this document are normative.

## 3. Terminology

**Local File Header (LFH).** A ZIP record that precedes each entry's payload inside the archive.

**Central Directory (CD).** A table at the end of a ZIP archive listing all entries.

**End of Central Directory Record (EOCD).** The final ZIP record in a non-commented archive. It points to the Central Directory or, in ZIP64 archives, is paired with ZIP64 end records.

**Index entry.** The first ZIP entry in a cozip archive. Its filename is always `__cozip__`. Its payload holds the cozip index.

**Index.** The binary structure stored in the payload of the index entry, defined in section 7.

**Priority file.** A file the creator chose to list in the cozip index. Priority files are reachable by a cozip-aware reader without reading the Central Directory.

**Non-priority file.** A file present as a ZIP entry inside the archive but not listed in the cozip index. Non-priority files are reachable through the Central Directory or through profile metadata that names their byte ranges.

**Payload.** The raw bytes of a ZIP entry's content, immediately following that entry's Local File Header. Since cozip forbids compression, encryption, data descriptors, and explicit directory entries, the payload is exactly the source file's bytes.

**Payload offset.** The byte position, measured from byte 0 of the archive, where a ZIP entry's payload begins.

**Half-open range.** An internal byte interval written `[start, end)`, including `start` and excluding `end`.

**Profile.** A named convention layered on top of the core format that fixes the names and roles of certain priority files.

## 4. Goals

1. Provide fast remote access to priority files inside a ZIP archive.
2. Remain a valid ZIP archive readable by standard ZIP tools.
3. Keep the format parseable in any language with minimal dependencies.
4. Support ZIP64 for large archives while keeping the index entry fixed and simple.
5. Prohibit ZIP features that break direct byte-range access: compression, encryption, data descriptors, split/spanned archives, explicit directory entries, and zero-byte entries.
6. Detect accidental structural modification of the cozip access path through a compact integrity hash.
7. Allow higher-level conventions through profiles without changing the core binary index.

## 5. Archive layout

A cozip archive is a valid, single-segment ZIP archive whose first entry is the index entry.

Byte 0 of the archive is the first byte of the Local File Header of the index entry. No header, padding, spanning marker, self-extracting executable, or metadata precedes it.

The top-level layout is:

```text
[LFH for __cozip__][cozip index payload]
[file entry 1]
[file entry 2]
...
[file entry n]
[Central Directory]
[optional ZIP64 EOCD record and ZIP64 EOCD locator]
[EOCD]
```

The ZIP archive comment length in the EOCD **MUST** be zero. Therefore, the EOCD is the final ZIP record and the final bytes of the archive.

### 5.1 Core requirements

1. The first ZIP entry **MUST** have filename `__cozip__` encoded as the 9 ASCII bytes `5F 5F 63 6F 7A 69 70 5F 5F`.
2. The first four archive bytes **MUST** be the ZIP Local File Header signature `0x04034b50` in little-endian order.
3. The Local File Header of the index entry **MUST** contain exactly one extra field: the cozip integrity hash block defined in section 8. The extra field length is therefore exactly 12 bytes.
4. The Local File Header of the index entry has a fixed size of 51 bytes: 30 fixed ZIP bytes, 9 filename bytes, and 12 extra-field bytes. The cozip index payload always begins at archive byte 51.
5. The index entry **MUST NOT** use ZIP64. Its classic 32-bit `compressed_size` and `uncompressed_size` fields **MUST** be equal, greater than zero, and less than `0xFFFFFFFF`.
6. Every ZIP entry **MUST** use compression method `0` (STORE).
7. Every ZIP entry **MUST** be unencrypted. General Purpose Bit Flag bit 0 **MUST NOT** be set. Strong-encryption related bits 6 and 13 **MUST NOT** be set. Compression method `99` **MUST NOT** be used. Archive-level Central Directory Encryption **MUST NOT** be used.
8. General Purpose Bit Flag bit 3 (Data Descriptor) **MUST NOT** be set for any entry. Data Descriptors after payloads are not allowed.
9. Every ZIP entry **MUST** have a payload size greater than zero. Explicit directory entries are not allowed, because directory entries have no payload.
10. All ZIP entry filenames in the archive **MUST** be unique.
11. All ZIP entry filenames **MUST** be valid UTF-8 when interpreted as path strings. For any filename containing non-ASCII bytes, General Purpose Bit Flag bit 11 (UTF-8 language encoding) **MUST** be set in both the Local File Header and the Central Directory Header. Writers **SHOULD** set bit 11 for all filenames.
12. Every filename listed in the cozip index **MUST** correspond to exactly one ZIP entry with the same filename, payload offset, and payload size.
13. The archive **MUST** be a single-segment ZIP file. Split, spanned, and multi-disk ZIP archives are not allowed.
14. The EOCD archive comment length **MUST** be zero.
15. The archive size **MUST** be at least `32768 + 51` bytes. This prevents the integrity-hash bytes at archive offsets 43 through 50 from falling inside the mandatory 32 KiB verification suffix.

### 5.2 ZIP64 policy

cozip supports ZIP64 for non-index entries and for the archive-level Central Directory structures.

The following rules apply:

1. The `__cozip__` index entry **MUST NOT** use ZIP64 and **MUST NOT** contain a ZIP64 extra field.
2. A non-index entry **MAY** use ZIP64 when its size, Local File Header offset, Central Directory offset, entry count, or Central Directory size cannot be represented in classic ZIP fields.
3. If a classic ZIP size or offset field is set to `0xFFFFFFFF`, or a classic ZIP count or disk field is set to `0xFFFF`, the corresponding ZIP64 extra field or ZIP64 end record **MUST** provide the real value.
4. For non-index entries using STORE mode, the resolved compressed size and resolved uncompressed size **MUST** be equal to each other and **MUST** be greater than zero.
5. If ZIP64 is required by the size of the archive, the number of entries, or the position of the Central Directory, the writer **MUST** emit the ZIP64 End of Central Directory Record and ZIP64 End of Central Directory Locator required by the ZIP format.
6. ZIP64 split or multi-disk features are still prohibited. All ZIP64 disk-number fields **MUST** describe a single-disk archive.

### 5.3 Name requirements

A cozip filename is a UTF-8 string stored in the ZIP filename field.

1. A filename **MUST NOT** be empty.
2. A filename **MUST NOT** start with `/`.
3. A filename **MUST NOT** contain a drive-letter prefix such as `C:`.
4. A filename **MUST** use `/` as the path separator when a path separator is needed.
5. A filename **MUST NOT** be `.` or `..`, and **MUST NOT** contain `/.` or `/..` path components.
6. The name `__cozip__` is reserved for the index entry and **MUST NOT** be used by any other entry.
7. Profiles may reserve additional names.

## 6. Writer model

This section is normative for writers.

A cozip writer **MUST** construct a final, internally consistent ZIP archive. The recommended model is planned or virtual construction: before writing bytes to the output object, the writer computes every Local File Header size, payload offset, payload size, ZIP64 requirement, profile metadata file, and Central Directory record.

A writer **MUST** know the final payload offsets and sizes before writing the `__cozip__` index payload. The index payload, its CRC-32, and its classic 32-bit size fields **MUST** be final when the index entry is emitted.

Only one post-write patch is permitted during finalization: replacing the 8 zero bytes of the cozip integrity hash field at archive offsets 43 through 50 with the computed integrity hash. This patch does not affect any ZIP CRC-32 because ZIP CRC-32 covers entry payload bytes, not Local File Header extra-field metadata.

The archive is considered finalized after the cozip writer has:

1. generated all profile metadata, including Parquet metadata where required;
2. written the index entry with final index payload bytes;
3. written all non-index entries;
4. written the Central Directory and EOCD or ZIP64 EOCD structures;
5. computed and patched the cozip integrity hash.

After finalization, the archive **MUST NOT** be modified.

## 7. Index layout

The index is the payload of the `__cozip__` entry. Its size equals the resolved `compressed_size` of the index entry, which equals its resolved `uncompressed_size` because STORE mode applies no transformation.

All multi-byte integers in the cozip index are encoded in little-endian byte order.

The index is organised as five contiguous sections.

```text
┌──────────────────────────────────────────────────────────────┐
│ Section 1. Header                         11 bytes           │
├──────────────────────────────────────────────────────────────┤
│ Section 2. Name lengths                   n_entries × 2      │
├──────────────────────────────────────────────────────────────┤
│ Section 3. Names (UTF-8 concatenated)     sum(name_lens) B   │
├──────────────────────────────────────────────────────────────┤
│ Section 4. Offsets                        n_entries × 8      │
├──────────────────────────────────────────────────────────────┤
│ Section 5. Sizes                          n_entries × 8      │
└──────────────────────────────────────────────────────────────┘
```

### 7.1 Header

| Offset | Size | Field       | Type   | Value                                   |
|-------:|-----:|-------------|--------|-----------------------------------------|
|      0 |    4 | `magic`     | bytes  | ASCII `CZIP` = `0x43 0x5A 0x49 0x50`    |
|      4 |    2 | `version`   | u16 LE | `1` for this binary format              |
|      6 |    1 | `profile`   | u8     | Profile identifier; see Part II         |
|      7 |    4 | `n_entries` | u32 LE | Number of priority files in the index   |

The header is 11 bytes long. A reader **MUST** reject the archive if `magic` is not `CZIP` or if `version` is greater than the reader supports.

A reader that does not recognise the declared `profile` **MAY** still parse the index and access priority files by literal name, but **MUST NOT** assume profile-specific semantics.

### 7.2 Name lengths

`n_entries` consecutive u16 little-endian values, giving the byte length of each name in section 7.3.

Each name length **MUST** be greater than zero.

### 7.3 Names

The concatenation of all entry names in the same order as section 7.2. Names are encoded in UTF-8 without null terminators.

Each name **MUST** be valid UTF-8. Names **MUST** be unique within the index. Each indexed name **MUST** correspond to exactly one ZIP entry in the archive.

The index **MUST NOT** include `__cozip__`.

### 7.4 Offsets

`n_entries` consecutive u64 little-endian values. Each value is the payload offset of the corresponding file, measured in bytes from byte 0 of the archive.

The offset points to the first byte of the file's payload, immediately after its Local File Header.

The order of entries in the index has no semantic meaning unless a profile adds a profile-specific ordering rule.

### 7.5 Sizes

`n_entries` consecutive u64 little-endian values. Each value is the byte length of the corresponding file's payload.

Each indexed size **MUST** be greater than zero. Because every cozip entry uses STORE mode, this value equals the resolved compressed size and the resolved uncompressed size of the corresponding ZIP entry.

For every indexed entry, `offset + size` **MUST** be less than or equal to the archive size and **MUST NOT** overflow u64 arithmetic.

## 8. Cozip integrity hash block

The cozip integrity hash block is a ZIP extra field attached to the Local File Header of the `__cozip__` entry. It carries an FNV-1a 64-bit hash over the cozip index payload and the final 32 KiB of the archive.

The hash is a compact structural integrity check. It is designed to detect accidental modification of the fast access path: the index payload, the ZIP tail, the EOCD, and ZIP64 end structures. It is not a cryptographic signature and is not a full payload checksum for every data byte in the archive.

### 8.1 Block layout

The hash block is exactly 12 bytes long.

| Offset | Size | Field            | Type   | Value                                  |
|-------:|-----:|------------------|--------|----------------------------------------|
|      0 |    2 | `header_id`      | u16 LE | `0xCA0C`                               |
|      2 |    2 | `data_size`      | u16 LE | `8`                                    |
|      4 |    8 | `integrity_hash` | u64 LE | FNV-1a 64 over the hash input below    |

Within the archive, the hash block occupies bytes 39 through 50 inclusive. The `integrity_hash` value itself occupies bytes 43 through 50.

### 8.2 Hash input

Let:

```text
INDEX_OFFSET      = 51
HASH_WINDOW_SIZE  = 32768
index_size        = resolved compressed size of the __cozip__ entry
index_region      = [INDEX_OFFSET, INDEX_OFFSET + index_size)
suffix_region     = [archive_size - HASH_WINDOW_SIZE, archive_size)
```

The archive size **MUST** be at least `HASH_WINDOW_SIZE + INDEX_OFFSET`.

The hash input is the union of `index_region` and `suffix_region`, hashed in ascending archive-byte order. If the two regions overlap, overlapping bytes are included only once.

Because the suffix region is 32 KiB and the ZIP archive comment is prohibited, the suffix region includes the EOCD and, when present, the ZIP64 EOCD Locator and ZIP64 EOCD Record.

### 8.3 Hash function

FNV-1a 64-bit is used because it is tiny to implement, fast, and sufficient for accidental corruption detection of a small structural window.

```text
offset_basis = 0xCBF29CE484222325
prime        = 0x100000001B3

h = offset_basis
for each byte b in input:
    h = h XOR b
    h = h × prime  (mod 2^64)
return h
```

### 8.4 Writer procedure

A writer **MUST** initially write the cozip integrity hash field as eight zero bytes.

After the full archive has been written, the writer **MUST** compute FNV-1a 64 over the hash input defined in section 8.2 and overwrite the 8 bytes at archive offsets 43 through 50 with the computed value.

No ZIP CRC-32 is affected by this write, because the patched bytes are in the Local File Header extra field of the `__cozip__` entry, not in any entry payload.

### 8.5 Reader verification

A reader **SHOULD** validate the integrity hash before trusting indexed offsets when the archive is read from remote storage or an untrusted cache.

To validate:

1. Read and validate the `__cozip__` Local File Header.
2. Read the complete index payload.
3. Obtain the archive size from `Content-Length`, `Content-Range`, filesystem metadata, or an equivalent byte-exact source.
4. Reject the archive if `archive_size < 32768 + 51`.
5. Read the last 32768 bytes of the archive.
6. Compute FNV-1a 64 over the union of the index region and suffix region as defined in section 8.2.
7. Compare the computed value with `integrity_hash` read from archive bytes 43 through 50.

If the values differ, the reader **SHOULD** reject the archive with `HASH_MISMATCH`.

## 9. Semantics and guarantees

1. **Immutable after finalization.** All offsets are absolute from byte 0. After the writer finalizes the archive, the archive is immutable.
2. **Single segment.** A cozip is one byte-addressable object, not a split or spanned ZIP.
3. **Unique names.** Every ZIP entry filename is unique across the entire archive.
4. **Direct payload access.** Because compression, encryption, data descriptors, and zero-byte entries are prohibited, every payload byte range is directly readable.
5. **ZIP64-capable.** Non-index entries and archive-level records may use ZIP64 when required.
6. **Partial index.** The index lists only priority files. Non-priority files may exist and are reachable through the Central Directory or profile metadata.
7. **Valid ZIP.** A cozip archive remains a valid ZIP archive and can be read by standard ZIP tools that support the ZIP features actually used by the archive.
8. **Structurally verifiable.** The cozip integrity hash checks the index payload and the ZIP tail. It detects accidental corruption of the fast access path but does not replace full payload checksums, cryptographic signatures, or application-level integrity metadata.
9. **Profile-agnostic core.** The core format makes no assumption about the roles of priority files beyond uniqueness and addressability. Profiles add such assumptions on top.

## 10. HTTP and byte-range access

This section is normative for remote cozip readers and for services that claim to serve cozip byte ranges correctly.

cozip offsets are defined over the exact bytes of the ZIP object. Therefore, remote storage **MUST** expose a byte-exact representation of the archive.

1. A server **MUST NOT** apply transparent HTTP content coding such as gzip or Brotli to the `.cozip` object when serving byte ranges. If `Content-Encoding` is present and changes the representation bytes, cozip offsets are invalid for that response.
2. A reader **SHOULD** request byte ranges using HTTP `Range` over the `bytes` range unit.
3. Internal cozip ranges are written as half-open intervals `[offset, offset + size)`.
4. HTTP byte ranges use an inclusive final byte position. Therefore, a cozip payload with `size > 0` at `offset` is requested as:

```http
Range: bytes=<offset>-<offset + size - 1>
```

5. Since zero-byte entries are prohibited, readers never issue invalid payload ranges such as `bytes=offset-(offset-1)`.
6. To request the first 64 KiB of an archive, a reader uses:

```http
Range: bytes=0-65535
```

7. To request the final 32 KiB for integrity verification, a reader may use a suffix range:

```http
Range: bytes=-32768
```

or, if the archive size is already known:

```http
Range: bytes=<archive_size - 32768>-<archive_size - 1>
```

## 11. Recommended reading procedure

This section is informative, but a conforming reader must still enforce the normative requirements referenced by the steps.

### Step 1. Fetch the leading region

Issue a byte-range request for the first 64 KiB of the archive:

```http
Range: bytes=0-65535
```

This range is expected to contain the fixed Local File Header of the index entry and the complete index payload in typical use.

### Step 2. Validate the index Local File Header

At archive byte 0, validate:

1. LFH signature is `0x04034b50`.
2. General Purpose Bit Flag bits 0, 3, 6, and 13 are unset.
3. Compression method is `0`.
4. `compressed_size` and `uncompressed_size` are equal, greater than zero, and less than `0xFFFFFFFF`.
5. Filename length is `9`.
6. Extra field length is `12`.
7. Filename is exactly `__cozip__`.
8. Extra field header ID is `0xCA0C`.
9. Extra field data size is `8`.

Read `integrity_hash` from archive bytes 43 through 50.

### Step 3. Locate the index payload

The index payload begins at byte 51. Its length is the 32-bit `compressed_size` from the index entry's Local File Header.

If the full payload `[51, 51 + index_size)` was not included in Step 1, issue a second byte-range request for:

```http
Range: bytes=51-<50 + index_size>
```

### Step 4. Parse the index

Starting at byte 51, parse the index header, name lengths, names, offsets, and sizes as described in section 7.

Validate:

1. magic is `CZIP`;
2. version is supported;
3. all names are valid UTF-8;
4. all names are unique;
5. all indexed sizes are greater than zero;
6. all `offset + size` ranges are within the archive size when the archive size is known.

### Step 5. Optionally verify structural integrity

Fetch the final 32 KiB of the archive and validate the integrity hash as described in section 8.5.

### Step 6. Access a priority file

For a given priority filename, find its index position by comparing against the Names section. Use that position to read `offset` and `size`.

Request the payload as:

```http
Range: bytes=<offset>-<offset + size - 1>
```

The returned bytes are the raw file contents.

### Step 7. Optional full ZIP validation

A strict validator may also read the Central Directory and confirm that each indexed filename has exactly one matching Central Directory entry, that the Central Directory's Local File Header offset resolves to a Local File Header whose computed payload offset matches the indexed offset, and that the resolved ZIP sizes match the indexed size.

This validation is not required for ordinary priority-file reads.

## 12. Error codes

Implementations SHOULD map parse and validation failures to the following canonical names.

| Code                              | Meaning |
|-----------------------------------|---------|
| `INVALID_LFH`                     | The index Local File Header is malformed or not at byte 0. |
| `INVALID_MAGIC`                   | Index header magic is not `CZIP`. |
| `UNSUPPORTED_VERSION`             | Index header version is greater than the reader supports. |
| `UNKNOWN_PROFILE`                 | Index header declares a profile the reader does not support and profile semantics are required. |
| `INVALID_HASH_BLOCK`              | The index LFH extra field is not the required cozip integrity hash block. |
| `HASH_MISMATCH`                   | The computed integrity hash does not match the stored value. |
| `ARCHIVE_TOO_SMALL`               | The archive is smaller than `32768 + 51` bytes. |
| `TRUNCATED_INDEX`                 | Index payload is shorter than required by `n_entries`. |
| `INVALID_INDEX_SIZE`              | The index entry has zero size, mismatched sizes, or a ZIP64 sentinel size. |
| `DUPLICATE_NAME`                  | Two entries in the index share the same name. |
| `DUPLICATE_ZIP_NAME`              | Two ZIP entries in the archive share the same filename. |
| `INVALID_NAME`                    | A filename violates cozip name requirements. |
| `INVALID_UTF8`                    | A name is not valid UTF-8 or a non-ASCII ZIP filename lacks required UTF-8 signaling. |
| `MISSING_ENTRY`                   | An indexed name has no corresponding ZIP entry. |
| `INVALID_RANGE`                   | An offset/size pair overflows or is internally inconsistent. |
| `OFFSET_OUT_OF_RANGE`             | An indexed payload range extends beyond the archive size. |
| `INVALID_SIZE`                    | A ZIP entry has zero payload size or mismatched resolved compressed/uncompressed sizes. |
| `DIRECTORY_ENTRY_NOT_ALLOWED`     | The archive contains an explicit directory entry. |
| `UNSUPPORTED_COMPRESSION_METHOD`  | A ZIP entry uses a compression method other than STORE. |
| `DATA_DESCRIPTOR_NOT_ALLOWED`     | General Purpose Bit Flag bit 3 is set or a data descriptor is present. |
| `ENCRYPTED_ENTRY`                 | A ZIP entry or the Central Directory uses encryption. |
| `SPLIT_ARCHIVE`                   | The archive is split, spanned, multi-disk, or starts with a spanning marker. |
| `INVALID_ZIP64`                   | A ZIP64 sentinel is present without a valid corresponding ZIP64 value, or ZIP64 values are inconsistent. |
| `INDEX_USES_ZIP64`                | The `__cozip__` index entry uses ZIP64, which is prohibited. |
| `HTTP_RANGE_NOT_BYTE_EXACT`       | The remote response is content-encoded or otherwise not byte-exact for cozip offsets. |

---

## Part II. Profiles

## 13. Profile mechanism

A profile is a named convention layered on top of the core format. It fixes the names and roles of certain priority files so a reader that recognises the profile can rely on a known shape without inspecting producer-specific documentation.

A cozip declares its profile through the `profile` field of the index header.

### 13.1 Profile values

The following profile values are reserved by this specification:

| Value | Profile | Section |
|------:|---------|---------|
|     0 | None    | 13.2    |
|     1 | Flat    | 14      |
|     2 | TACO    | 15      |

Values from `3` to `255` are reserved for future use and **MUST NOT** be assigned by external specifications unless this document or a successor specification registers them.

### 13.2 Profile 0: None

A value of `0` means the cozip conforms to no profile. Priority filenames are free except for names reserved by the core format. Readers **MUST** inspect priority files by literal name as documented by the producer.

### 13.3 What a profile may do

A profile **MAY**:

1. reserve additional filenames;
2. require that certain priority files exist;
3. define expected formats and schemas for those files;
4. define the layout or naming of non-priority files;
5. require certain priority files to appear at specific positions in the archive;
6. declare a profile-specific file extension or MIME convention.

### 13.4 What a profile must not do

A profile **MUST NOT**:

1. modify the binary structure of the cozip index;
2. modify the Local File Header layout of the `__cozip__` entry;
3. override or relax any requirement of Part I;
4. reassign or redefine an already registered profile value.

### 13.5 Reader behaviour

A reader that does not support the declared profile **MAY** still parse the cozip index and access priority files by literal name, but **MUST NOT** make assumptions about their roles or contents.

A reader **MAY** raise `UNKNOWN_PROFILE` and refuse to proceed if profile-specific guarantees are required by the application.

A reader that supports the declared profile **SHOULD** validate the profile-specific requirements before exposing profile semantics to its caller.

## 14. Flat profile

The Flat profile describes a cozip whose priority metadata forms a single flat manifest for the archive's data entries.

### 14.1 Profile value

`profile = 1`.

### 14.2 Priority files

A Flat-profile cozip **MUST** contain exactly one priority file in its index:

```text
__metadata__
```

`__metadata__` is a Parquet file.

### 14.3 Position requirement

`__metadata__` **MUST** be the last file entry in the archive before the Central Directory.

The `__metadata__` file contains offsets and sizes for other entries, so writers normally construct it from the planned cozip layout before final archive emission.

### 14.4 Schema

The `__metadata__` Parquet file **MUST** contain one row for every ZIP data entry except:

1. the `__cozip__` index entry; and
2. the `__metadata__` entry itself.

The Parquet file **MUST** contain at least the following columns:

| Column   | Type   | Meaning |
|----------|--------|---------|
| `name`   | string | The entry's filename in the archive. Must be unique. |
| `offset` | uint64 | The entry's payload offset, from byte 0 of the archive. |
| `size`   | uint64 | The entry's payload byte length. Must be greater than zero. |

The `offset` and `size` columns are computed by the writer at archive-creation time. The `name` column matches the filename of the corresponding ZIP entry.

A producer **MAY** add additional columns to carry user-defined metadata. The names `name`, `offset`, and `size` are reserved by this profile.

### 14.5 File extension

A Flat-profile cozip uses the file extension `.cozip`. The MIME type is `application/zip`.

## 15. TACO profile

The TACO profile describes a cozip whose priority files form the ZIP-mode access contract of a TACO dataset.

### 15.1 Profile value

`profile = 2`.

### 15.2 Priority files

A TACO-profile cozip **MUST** contain the following priority files in its index:

1. `COLLECTION.json`;
2. every Parquet file under the `METADATA/` directory.

A TACO-profile cozip **MUST NOT** include explicit directory entries for `DATA/` or `METADATA/`.

### 15.3 Position and contiguity requirement

All TACO priority files **MUST** appear after every entry under `DATA/` in the archive.

All TACO priority files **MUST** form one contiguous final file-entry block immediately before the Central Directory. No non-priority entry may appear between two TACO priority entries, and no non-priority entry may appear after the TACO priority block.

This guarantees that the priority payloads are addressable through one contiguous archive region. A reader may compute:

```text
priority_region_start = min(priority_payload_offsets)
priority_region_end   = max(priority_payload_offset + priority_payload_size)
```

and fetch:

```http
Range: bytes=<priority_region_start>-<priority_region_end - 1>
```

The returned region may contain Local File Headers between priority payloads. The reader slices out each payload using the offsets and sizes from the cozip index.

### 15.4 Reference semantics

The semantics of `COLLECTION.json`, the Parquet files under `METADATA/`, the `DATA/` layout, TACO metadata columns such as `internal:offset` and `internal:size`, and the dataset contract are defined by the TACO specification.

This cozip profile only defines how the TACO ZIP-mode priority files are made byte-addressable through the cozip index.

### 15.5 File extension

A TACO-profile cozip uses the file extension `.cozip`. The MIME type is `application/zip`.

---

## Appendices

## Appendix A. MIME type and file extension

A cozip archive uses the file extension `.cozip` regardless of profile. The MIME type is `application/zip`, since a cozip is a valid ZIP archive.

When serving `.cozip` over HTTP, servers must preserve byte-exact object bytes for range requests. Transparent content encoding is incompatible with cozip byte offsets.

## Appendix B. Version history

| Version      | Date       | Changes |
|-------------:|------------|---------|
| 1.0-draft.1  | 2026-04-27 | Initial draft. |
| 1.0-draft.2  | 2026-04-27 | Added ZIP64 policy for non-index entries; explicitly prohibited encryption, split/spanned archives, data descriptors, directory entries, and zero-size entries; changed the integrity hash to cover the index payload plus the final 32 KiB; fixed HTTP byte-range inclusivity; made UTF-8 and filename uniqueness requirements explicit; fixed Flat profile `__metadata__` semantics; made TACO priority files contiguous; replaced pending TACO reference. |

## Appendix C. References

1. PKWARE Inc. **.ZIP File Format Specification**, APPNOTE.TXT, version 6.3.10, 2022. https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
2. IETF. **HTTP Semantics**, RFC 9110, 2022. https://datatracker.ietf.org/doc/html/rfc9110
3. Bradner, S. **Key words for use in RFCs to Indicate Requirement Levels**, RFC 2119, 1997. https://datatracker.ietf.org/doc/html/rfc2119
4. Leiba, B. **Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words**, RFC 8174, 2017. https://datatracker.ietf.org/doc/html/rfc8174
5. Yergeau, F. **UTF-8, a transformation format of ISO 10646**, RFC 3629, 2003. https://datatracker.ietf.org/doc/html/rfc3629
6. Fowler, G., Noll, L. C., Vo, K. P. **FNV Hash**, non-cryptographic hash function. http://www.isthe.com/chongo/tech/comp/fnv/
7. Apache Software Foundation. **Apache Parquet Format**. https://parquet.apache.org/
8. Asterisk Labs. **The TACO Specification**, version 3.0.0, released 2026-04-13. https://asterisk.coop/taco/spec/
