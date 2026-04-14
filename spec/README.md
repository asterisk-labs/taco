# 🌮 TACO Specification v3.0.0

The formal specification for TACO (Transparent Access to Cloud-Optimized datasets), a format for packaging AI-ready Earth Observation datasets.

## Files

```
├── index.html              ← specification document
├── style.css               ← embedded (inline)
└── assets/
    ├── datamodel.png       ← Figure 1: Data Model
    ├── dataset.png         ← Figure 2: Physical Layout
    ├── tacotoolbox.png     ← Figure 3: Writer API
    ├── tacoreader.png      ← Figure 4: Reader API
    ├── isp_logo.png
    ├── leipzig_logo.png
    ├── tum_logo.png
    └── asterisk_logo.svg
```

## Usage

Open `index.html` in a browser. No build step required.

## Sections

1. Version and Schema
2. Overview
3. Foundations (Parquet, VSI, Cloud-Optimized ZIP)
4. Design Goals and Tradeoffs
5. Data Model (Contract, Structure, Metadata, Collection)
6. Dataset Versioning (SemVer)
7. Physical Layer (Directory, Parquet, ZIP, FOLDER, TACOCAT)
8. API Layer (TacoToolbox, DuckDB Extension, Thin Wrappers)
- Annex A: Migration from v2
- Annex B: History

## Links

- [asterisk.coop/taco](https://asterisk.coop/taco)
- [source.coop/taco](https://source.coop/taco)
- [github.com/asterisk-labs/taco](https://github.com/asterisk-labs/taco)

## License

MIT · [Asterisk Labs](https://asterisk.coop)