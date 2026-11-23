# TACO Specification v3.0.0

The formal specification for TACO (Transparent Access to Cloud-Optimized datasets), a
format for packaging Earth observation datasets.

## Files

```
├── SPEC.md                  normative source
├── index.html               generated from SPEC.md
├── extensions/<name>.md     one document per extension
├── extensions/<name>.html   generated from it
├── extensions/<name>/       the extension's JSON Schema and an example
└── assets/                  figures
```

## Usage

Edit `SPEC.md` or an extension document, then run
`python docs/_build/build.py --sync-spec --output _site --clean` from the repository
root. It renders the Markdown into the HTML pages beside it and into `_site/spec/`.
The HTML is generated, not an independent copy of the specification.

## Sections

1. Version
2. Overview
3. Foundations (Parquet, VSI, Cloud-Optimized ZIP)
4. Design Goals and Tradeoffs
5. Data Model (Contract, Structure, Metadata, Collection, Extensions)
6. Dataset Identity and Mutability
7. Physical Layer (Directory, Internal Columns, ZIP, FOLDER, TACOCAT, Validation)
8. API Layer (writer, reader, Dataset API)

## Links

- [asterisk.coop/taco](https://asterisk.coop/taco)
- [source.coop/taco](https://source.coop/taco)
- [github.com/asterisk-labs/taco](https://github.com/asterisk-labs/taco)

## License

MIT · [Asterisk Labs](https://asterisk.coop)
