# TACO v3 — Technical Deck

🚧 **Work in progress**

Deep-dive deck for the [TACO v3 specification](http://asterisk.coop/taco/spec). Aimed at developers and researchers who want to understand the internals.

## Planned slides

- Contract formalization (σ, μ) with fixed and variable leaves
- Parquet schema: internal columns, parent_id linking, level naming
- Cloud-optimized ZIP internals: `__coz_index__`, STORE mode, CDC
- read_taco() API: idx, level, pivot parameters
- TACOCAT: cross-partition metadata consolidation
- Benchmarks: CD size vs COZ index, query performance at scale
- tacotoolbox writer API: Structure, Metadata, Contract, Sample, Asset

## License

MIT