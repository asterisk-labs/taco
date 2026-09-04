# cozip Playground

This directory contains a dependency-free browser playground for understanding
how a cozip archive is planned and written.

The production implementation now lives in the separate
[asterisk-labs/cozip](https://github.com/asterisk-labs/cozip) project. This
playground intentionally remains an educational JavaScript implementation; it
does not vendor or load cozip source code from inside the TACO repository.

Open the playground directly from the repository root:

```powershell
Start-Process deck\playground\index.html
```

No development server is required. The page does not upload files; selected
payloads stay in the current browser tab and the generated archive is downloaded
as a Blob.

To serve the repository over HTTP instead:

```powershell
python -m http.server 8000
```

Then open:

```text
http://localhost:8000/deck/playground/
```

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

This is an educational JavaScript implementation, not the production writer:

- no ZIP64 support;
- no compression, encryption, data descriptors, comments, or directories;
- no FLAT profile Parquet generation;
- no TACO profile validation;
- no streaming writer;
- no cryptographic authentication of payload bytes.

Use the official [cozip project](https://github.com/asterisk-labs/cozip) for the
C writer, Python/R/Julia bindings, JavaScript reader, format specification, and
published releases.

## GitHub Pages

The playground runs as a static GitHub Pages page. The TACO Pages
workflow publishes only TACO's own `deck/` files; cozip code and release
automation remain in the separate cozip repository.
