# 🌮 TACO Specification v3.0.0

The formal specification for TACO (Transparent Access to Cloud-Optimized datasets), a format for packaging Earth Observation datasets.

## Files

```
├── SPEC.md                 ← normative source
├── style.css               ← presentation only
└── assets/
    ├── isp_logo.png
    ├── leipzig_logo.png
    ├── tum_logo.png
    └── asterisk_logo.svg
```

## Usage

Edit `SPEC.md`, then run `make site` from the repository root. The generated
document is written to `_site/spec/index.html`; HTML is build output, not an
independent copy of the specification.

## Sections

1. Version and Schema
2. Overview
3. Foundations (Parquet, VSI, Cloud-Optimized ZIP)
4. Design Goals and Tradeoffs
5. Data Model (Contract, Structure, Metadata, Collection)
6. Dataset Versioning (SemVer)
7. Physical Layer (Directory, Parquet, ZIP, FOLDER, TACOCAT)
8. API Layer (taco writer, reader core, language clients)

## Links

- [asterisk.coop/taco](https://asterisk.coop/taco)
- [source.coop/taco](https://source.coop/taco)
- [github.com/asterisk-labs/taco](https://github.com/asterisk-labs/taco)

## License

MIT · [Asterisk Labs](https://asterisk.coop)
