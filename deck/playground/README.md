# cozip Playground

This directory contains a dependency-free browser playground for understanding
how a cozip archive is planned and written.

Open it directly from the repository root:

```powershell
Start-Process deck\playground\index.html
```

No development server is required. The page does not upload files; selected
payloads stay in the current browser tab and the generated archive is downloaded
as a Blob.

## What The Playground Builds

The playground builds a valid core cozip archive using profile `NONE` (`0`):

1. It accepts one or more non-empty browser `File` payloads.
2. It normalizes archive paths and avoids reserved names.
3. It plans Local File Header offsets before writing bytes.
4. It writes the fixed first entry named `__cozip__`.
5. It serializes the `CZIP` index payload at byte `51`.
6. It writes every selected file as a ZIP STORE entry.
7. It writes the Central Directory and EOCD.
8. It computes FNV-1a 64 over the index region and final 32 KiB.
9. It patches the hash into archive bytes `43..50`.

For small inputs, it adds a non-indexed `__cozip_padding__` entry so the archive
meets the cozip minimum size of `32768 + 51` bytes.

## What It Does Not Build

This is an educational JavaScript implementation, not the production C writer:

- no ZIP64 support;
- no compression, encryption, data descriptors, comments, or directories;
- no FLAT profile Parquet generation;
- no TACO profile validation;
- no streaming writer;
- no cryptographic authentication of payload bytes.

The Python and R bindings are still the current high-level path for FLAT cozip
archives. The C core remains the authority for production writer semantics.

## Relationship To WASM

A fully client-side cozip converter is feasible with WebAssembly. The C library
already exposes the functions needed by a browser wrapper:

- `cozip_plan`
- `cozip_index_payload_size`
- `cozip_build_index_payload`
- `cozip_build_extra_field`
- `cozip_write_archive`
- `cozip_patch_integrity_hash`

The missing layer is an ergonomic JavaScript or TypeScript API over the raw
Emscripten module. That wrapper needs to:

- load `cozip/javascript/wasm/cozip.js` and `cozip.wasm`;
- expose `FS`, `HEAPU8`, `_malloc`, and `_free`;
- copy browser `File` bytes into MEMFS or WASM memory;
- allocate and fill `cozip_entry_t` records with correct struct offsets;
- call the writer pipeline in order;
- read the output archive from MEMFS;
- return a Blob or `Uint8Array` to the browser application.

The playground's pure JavaScript writer is useful for inspection and demos while
that wrapper is not yet available. The WASM path should eventually replace the
manual ZIP assembly when the project needs browser parity with the native C
writer.
