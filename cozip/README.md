<div align="center">
  <img src="images/cozip_logo.png" alt="cozip" width="180"/>

  # Cloud Optimized ZIP
  
  *Random access over HTTP. Open a ZIP like a table.*

  [![License MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
  [![C](https://img.shields.io/badge/Language-C-A8B9CC.svg?logo=c&logoColor=white)]()
  [![DuckDB](https://img.shields.io/badge/DuckDB-Extension-FFF000.svg?logo=duckdb&logoColor=black)]()
  [![Spec v1.0](https://img.shields.io/badge/Spec-v1.0--draft.2-brightgreen.svg)](SPEC.md)
</div>

---

<p align="center">
  <img src="images/fig1_request_comparison.svg" alt="ZIP vs cozip range requests" width="100%"/>
</p>

## What is cozip

A cozip is a ZIP archive designed for direct access over the network.

It places a compact index at byte 0 with the offsets and sizes of selected files. A reader fetches that index in one request and jumps straight to the data it needs.

Everything else stays standard. Any ZIP tool can still open it.

In practice, cozip archives often include a `__metadata__` Parquet file that lists every entry with its name, offset, and size. Because it is just Parquet, tools like DuckDB, Arrow, or Polars can read it directly. This makes it possible to treat a cozip archive as a table and query its contents without scanning the whole file.

## License

MIT

<div align="center">
  <br>
  Developed with ❤️ by
  <br><br>
  <a href="https://asterisk.coop">
    <img src="images/asterisk_logo.svg" alt="Asterisk Labs" width="400"/>
  </a>
</div>