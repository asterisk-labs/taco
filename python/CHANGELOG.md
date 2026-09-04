# Changelog

All notable changes to `taco` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.2.0 - 2026-09-04

### Changed

- The package is laid out as subpackages: `taco.contract` for the contract
  vocabulary, `taco.writer` for the two writers and `taco.reader` for metadata
  access, with `tacocat` and `validate` beside them. The top level exports ten
  names, down from thirty-eight; everything else is imported from the
  subpackage that owns it.
- `COLLECTION.json` records only the fields the caller declares. Nothing is
  inferred from field names, so the writer no longer guesses an extent from
  `stac:centroid` or `time_start`, and `_extent.py`, `auto_extent`,
  `ExtentSpec`, `compute_extent` and `wkb_bounds` are gone. STAC is not part
  of the core. A TACOCAT still merges the extents its partitions declare,
  which TACO specification 7.5 requires.
- Archives are written as `.zip`. cozip specification 14.5 makes the profile
  byte in the byte-0 index the only authoritative signal, and the extension
  carries no meaning. Outputs are extensionless or end in `.zip`; any other
  suffix is rejected.
- Reading a dataset is the `cozip` DuckDB extension's job. `open_dataset`,
  `TacoDataset` and `vsi_subfile` are gone from the top-level API;
  `taco.reader` forwards to the extension and builds no VSI paths itself. The
  old Python mapping covered four URL schemes and the C++ one covers seven,
  so the two had already drifted.
- The ZIP writer now delegates layout planning and serialization to cozip's
  native TACO ABI through `cozip._taco.plan()` / `write()` instead of driving
  the low-level entry API itself. Archive names, priority ordering, padding
  placement and post-write verification are enforced in C. Local source and
  output paths may contain Unicode.
- `Contract`, `Collection` and the writers raise `taco.errors.*`
  exceptions (`ContractError`, `SampleError`, `CollectionError`,
  `WriterError`, ...). They still subclass `ValueError` / `RuntimeError`.
- Metadata levels without fields may be omitted from `Contract(metadata=...)`
  and from `Sample(metadata=...)`.
- `internal:relative_path` is written in `collection.parquet` too (the sample
  index), matching the normative text of spec section 7.2.
- Lossy value conversions (floats into integer fields, bytes into strings,
  ISO strings into timestamps) are rejected at `add()` time.

### Added

- `taco:structure = null` datasets (one file per sample) in every container.
- Inline `bytes` assets, materialized into the staging directory on `add()`.
- `FolderWriter` / `open_folder()` for FOLDER mode with `append=True` and
  optional hard links.
- Partitioned builds (`partition_size`, `partition_by`) producing
  `<stem>_partNNNN.zip` / `<stem>_<value>.zip` plus a `.tacocat` directory;
  `consolidate()` to build a TACOCAT from existing archives.
- An internal metadata view for ZIP, FOLDER and TACOCAT, used by
  `consolidate` and `validate`.
- `validate()` for structural and contract checks across all three containers.
- Type specification parser supporting `timestamp[unit, tz]`, `list<...>`,
  `struct<...>`, `map<...>`, `decimal128(p, s)`, `fixed_size_list<...>`.
- `parse_size()` and file-name sanitizing for partitioned builds.

### Fixed

- `overwrite=False` uses an atomic no-replace publication step, so a file
  created by another process during the build is left untouched.

### Removed

- Direct use of `cozip.profile()` and the entry-based `cozip_plan` /
  `cozip_finalize` calls.

## 0.1.0 - 2026-09-02

- First prototype: `Contract`, `Asset`, `Sample`, `Collection`, staged
  `TacoWriter` producing profile-2 `.tacozip` archives.
