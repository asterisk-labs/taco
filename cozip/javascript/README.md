# cozip - JavaScript / WebAssembly

Status: WASM build path and browser wrapper scaffold are present. The production
TypeScript package is not finished yet.

## What Works Today

The cozip C library can be compiled to WebAssembly with Emscripten. From the
repository root:

```bash
cd cozip
make wasm
```

On Windows PowerShell, GNU `make` is often not installed. Use the native script
instead:

```powershell
conda activate taco
.\cozip\build-wasm.ps1
```

If you are already inside the `cozip` directory, run:

```powershell
.\build-wasm.ps1
```

If Emscripten is installed but not active in the current terminal, pass the
`emsdk` checkout path and the script will import `emsdk_env.bat` before building:

```powershell
.\cozip\build-wasm.ps1 -EmsdkRoot C:\tools\emsdk
```

Both paths require Emscripten (`emcc` and `emcmake`) to be available on `PATH`.
The `taco` conda environment already has CMake and Ninja in the local setup, but
it does not currently have Emscripten.

The build writes generated artifacts under:

```text
cozip/javascript/wasm/cozip.js
cozip/javascript/wasm/cozip.wasm
```

Those files are ignored by git and regenerated on demand.

The target exports the low-level C ABI plus a small browser-oriented bridge:

```text
cozip_plan
cozip_index_payload_size
cozip_build_index_payload
cozip_write_archive
cozip_patch_integrity_hash
cozip_wasm_write_archive_from_buffers
```

The bridge lives in `wasm_bridge.c`. It accepts arrays of names, byte buffers,
sizes, and index flags, builds `cozip_entry_t` records in C, writes the archive
to Emscripten MEMFS, patches the integrity hash, and adds a non-indexed
`__cozip_padding__` entry when tiny inputs would otherwise violate the cozip
minimum size.

## Browser Wrapper

`src/cozip-wasm-wrapper.js` is a thin ES module over the generated Emscripten
module. It accepts browser `File`/`Blob` objects or `Uint8Array` payloads and
returns a generated ZIP as a `Uint8Array`.

Example from a page served at the repository root:

```html
<script src="/cozip/javascript/wasm/cozip.js"></script>
<script type="module">
  import { createCozipWasmWriter } from "/cozip/javascript/src/cozip-wasm-wrapper.js";

  const writer = await createCozipWasmWriter({
    locateFile: (path) => `/cozip/javascript/wasm/${path}`,
  });

  const bytes = await writer.writeArchive({
    entries: [
      { name: "data/a.txt", data: new TextEncoder().encode("hello\n"), inIndex: true },
    ],
  });

  const blob = new Blob([bytes], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  window.open(url);
</script>
```

Use a local HTTP server. Loading the generated `.wasm` through `file://` is
browser-dependent and often fails because Emscripten loads the binary with
`fetch`.

```powershell
python -m http.server 8000
```

Then open `http://localhost:8000/deck/playground/` or a dedicated WASM demo page.

## Current Scope

The wrapper supports the core `NONE` cozip profile cleanly: browser files become
stored ZIP entries, selected files are listed in the byte-zero index, and the
output remains normal ZIP.

It does not yet build FLAT/TACO semantics by itself:

- FLAT needs a generated `__metadata__` Parquet file.
- TACO needs profile-specific metadata validation.
- A browser Parquet writer such as Arrow JS or parquet-wasm would be needed for
  parity with the Python writer.

The current path is therefore:

1. Keep the pure JavaScript playground as an educational fallback.
2. Build `make wasm`.
3. Use `src/cozip-wasm-wrapper.js` for client-side core cozip conversion.
4. Add a Parquet metadata layer later for FLAT/TACO browser parity.

## Why WASM And Not Node Native Addons

WASM runs in browsers and also works in Node, Deno, and edge runtimes. A native
Node addon would need a separate binary per operating system and architecture.
The cozip C core already vendors libzip and zlib, so Emscripten reuses the same
writer implementation across those targets.
