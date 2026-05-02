# cozip — JavaScript / WebAssembly

Status: build pipeline ready, public API not yet written.

## What works today

The cozip C library compiles cleanly to WebAssembly via Emscripten
with no source changes. `make wasm` from the repository root produces:

    javascript/wasm/cozip.js     emscripten loader
    javascript/wasm/cozip.wasm   compiled module

The eight public cozip functions are exported and callable from
JavaScript:

    cozip_version_string
    cozip_status_string
    cozip_plan
    cozip_index_payload_size
    cozip_build_index_payload
    cozip_build_extra_field
    cozip_write_archive
    cozip_patch_integrity_hash

Plus `_malloc` and `_free` for heap management from JS.

## What is missing

There is no idiomatic TypeScript API on top of the raw WASM exports
yet. Anyone using the module today has to:

- allocate memory in the WASM heap with `_malloc`
- copy strings and structs byte-by-byte using `stringToUTF8` and
  manual offset arithmetic against the `cozip_entry_t` layout
- handle 64-bit values via `BigInt`
- free memory with `_free` after each call

Writing this wrapper is roughly 1–2 weeks of focused work. The shape
will be something like:

    import { writeArchive } from '@asterisk-labs/cozip';

    await writeArchive({
        out: 'data.zip',
        profile: 'flat',
        entries: [
            { name: 'metadata.parquet', source: parquetBytes, inIndex: true },
            { name: 'image_001.tif',    source: imageBytes },
        ],
    });

When a concrete use case appears (browser-native cozip reader, npm
package for downstream tooling, demo of TACO datasets in the
browser), this directory is where that work lives.

## Building locally

Requires Emscripten 3.x or newer. On macOS:

    brew install emscripten

Then from the repository root:

    make wasm

This runs `emcmake cmake` against `core/`, builds the static archives
under `core/build-wasm/`, and links them into `javascript/wasm/cozip.{js,wasm}`.

The output is regenerated on every `make wasm` and is not committed
to the repository.

## Why WASM and not a native node-gyp addon

Two reasons:

1. WASM is portable — one binary runs on Linux, macOS, Windows,
   browser, Node, Deno, Cloudflare Workers, edge runtimes. A native
   addon needs a separate build per platform-architecture pair.

2. The cozip C library is already standalone (libzip and zlib are
   vendored under `core/`). Going through Emscripten reuses that
   work directly — no separate node-gyp build chain to maintain.

The trade-off is that WASM has higher memory-management overhead
than a native addon for hot loops. For cozip's workload (writing
archive metadata, computing offsets) this is not a bottleneck.