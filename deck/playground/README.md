# cozip Playground

This directory contains a dependency-free browser playground for understanding
how a cozip archive is planned and written.

Open the educational fallback directly from the repository root:

```powershell
Start-Process deck\playground\index.html
```

No development server is required for the fallback writer. The page does not
upload files; selected payloads stay in the current browser tab and the
generated archive is downloaded as a Blob.

When the compiled WASM artifacts are available, serve the repository over HTTP:

```powershell
python -m http.server 8000
```

Then open:

```text
http://localhost:8000/deck/playground/
```

The playground will try to load:

```text
../../cozip/javascript/wasm/cozip.js
../../cozip/javascript/wasm/cozip.wasm
../../cozip/javascript/src/cozip-wasm-wrapper.js
```

If those files load, archive downloads are produced by the compiled C writer in
WebAssembly. If they do not load, the page falls back to the educational
JavaScript writer and keeps the visual explainer working.

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

## GitHub Pages

The playground can run as a static GitHub Pages page. No backend process is
needed. The browser fetches static HTML, CSS, JavaScript, and `.wasm` files, then
runs the writer locally.

For `https://asterisk.coop/taco/deck/playground/`, the expected static asset
paths are:

```text
https://asterisk.coop/taco/cozip/javascript/wasm/cozip.js
https://asterisk.coop/taco/cozip/javascript/wasm/cozip.wasm
https://asterisk.coop/taco/cozip/javascript/src/cozip-wasm-wrapper.js
```

`cozip/javascript/wasm/` is currently ignored by git because it is generated
build output. That is fine for source hygiene, but GitHub Pages still needs
those generated files in the deployed site. Use one of these approaches:

1. Build WASM in `.github/workflows/pages.yml` and upload the generated site
   artifact.
2. Force-add the generated `cozip.js` and `cozip.wasm` files only if the project
   wants committed browser binaries.

The workflow approach is cleaner because every deployment rebuilds WASM from the
current C source.

## Relationship To WASM

A fully client-side cozip converter is feasible with WebAssembly. The C library
already exposes the functions needed by a browser wrapper:

- `cozip_plan`
- `cozip_index_payload_size`
- `cozip_build_index_payload`
- `cozip_build_extra_field`
- `cozip_write_archive`
- `cozip_patch_integrity_hash`

The repository now includes a first wrapper scaffold in
`cozip/javascript/src/cozip-wasm-wrapper.js` and a C bridge in
`cozip/javascript/wasm_bridge.c`. That bridge keeps JavaScript from depending on
the internal `cozip_entry_t` struct layout.

The WASM path needs to:

- load `cozip/javascript/wasm/cozip.js` and `cozip.wasm`;
- expose `FS`, `HEAPU8`, `_malloc`, and `_free`;
- copy browser `File` bytes into MEMFS or WASM memory;
- call `cozip_wasm_write_archive_from_buffers`;
- read the output archive from MEMFS;
- return a Blob or `Uint8Array` to the browser application.

The playground automatically prefers the WASM path when the generated files are
present. The pure JavaScript writer remains useful for inspection, local file
opening, and demos where the generated WASM files are not deployed.
