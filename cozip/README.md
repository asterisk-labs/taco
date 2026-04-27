<div align="center">
  <img src="images/cozip_logo.png" alt="cozip" width="180"/>

  # cozip

  **A valid ZIP archive with a fast index at byte 0**<br>
  *Write once. Fetch once. Jump straight to the files you need.*

  [![License MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
  [![C](https://img.shields.io/badge/Language-C-A8B9CC.svg?logo=c&logoColor=white)]()
  [![DuckDB](https://img.shields.io/badge/DuckDB-Extension-FFF000.svg?logo=duckdb&logoColor=black)]()
  [![Spec v1.0](https://img.shields.io/badge/Spec-v1.0--draft.2-brightgreen.svg)](SPEC.md)
</div>

---

## What is cozip

A cozip is a normal ZIP archive with one extra idea.

The files you care about are listed in a compact binary index at byte 0. A reader fetches that index with one range request, gets the byte offsets and sizes, then jumps directly to the file it needs.

The rest of the ZIP stays valid. Standard ZIP tools still work.

## Why it exists

Standard ZIP puts its table of contents at the end of the file. On large archives that table can be huge, so a remote reader has to fetch a lot of data before it can open anything.

Cozip puts the fast access path at the front.

## Highlights

- Index at byte 0
- Valid ZIP archive
- STORE mode only
- Write once and immutable after creation
- Structural integrity hash
- ZIP64 capable
- Optional profiles for Flat and TACO datasets
- DuckDB extension for reading

# License

MIT

<div align="center">
  <br>
  Developed with ❤️ by
  <br><br>
  <a href="https://asterisk.coop">
    <img src="images/asterisk_logo.svg" alt="Asterisk Labs" width="400"/>
  </a>
</div>