# TACO

A specification for organizing Earth Observation datasets.

v3.0.0 in progress.

- [`spec/`](spec/) is the specification document (open `spec/index.html`).
- [`python/`](python/) is the reference Python writer, installed as `taco-eo`
  and imported as `taco`. It builds immutable `.zip` archives carrying cozip
  profile `TACO`, FOLDER datasets with incremental append and TACOCAT
  consolidations, and validates existing datasets.
  See [`python/README.md`](python/README.md).
- Reading is the [cozip DuckDB extension](https://github.com/asterisk-labs/cozip_reader),
  whose `read_taco()` serves Python, R and Julia from one C++ implementation.
- [`deck/`](deck/) and [`onepager/`](onepager/) are the public site material.

The cozip implementation and language bindings live in the separate
[asterisk-labs/cozip](https://github.com/asterisk-labs/cozip) project.
The writer reaches cozip's public native TACO ABI through the private Python
adapter `cozip._taco`, so it needs a cozip release that ships that adapter.
