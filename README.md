<div align="center">
  <img src="images/banner.svg" alt="TACO — AI-ready Earth Observation datasets." width="700"/>
  <p>
    <a href="spec/LICENSE"><img src="https://img.shields.io/badge/license-MIT-EAB308?style=flat-square" alt="License MIT"/></a>
    <a href="https://github.com/asterisk-labs/taco/actions/workflows/release.yml"><img src="https://img.shields.io/github/actions/workflow/status/asterisk-labs/taco/release.yml?label=python%20tests&style=flat-square" alt="Python tests"/></a>
    <a href="https://github.com/asterisk-labs/taco/actions/workflows/release.yml"><img src="https://img.shields.io/badge/python%20coverage-88%25-brightgreen?style=flat-square" alt="Python coverage 88%"/></a>
    <a href="https://pypi.org/project/taco-eo"><img src="https://img.shields.io/pypi/v/taco-eo?label=python&logo=python&logoColor=white&color=3776AB&style=flat-square" alt="Python"/></a>
    <a href="https://pypi.org/project/taco-eo"><img src="https://img.shields.io/pypi/pyversions/taco-eo?style=flat-square" alt="Supported Python versions"/></a>
    <a href="https://asterisk-labs.r-universe.dev/taco"><img src="https://img.shields.io/badge/r--universe-taco-276DC3?logo=r&logoColor=white&style=flat-square" alt="R"/></a>
    <a href="https://github.com/asterisk-labs/AsteriskRegistry"><img src="https://img.shields.io/badge/julia-Taco.jl-9558B2?logo=julia&logoColor=white&style=flat-square" alt="Julia"/></a>
    <a href="javascript/"><img src="https://img.shields.io/badge/javascript-v1.0.0-F7DF1E?logo=javascript&logoColor=black&style=flat-square" alt="JavaScript 1.0.0"/></a>
    <a href="https://asterisk.coop/taco/spec"><img src="https://img.shields.io/badge/spec-v3-A8B9CC?style=flat-square" alt="Specification"/></a>
  </p>
</div>

---

TACO packages Earth observation data, metadata, and sample structure as one
portable dataset. Query by region, time, cloud cover, split, or any other field
before loading a pixel.

Every dataset declares a contract for the files and metadata in each sample.
TACO validates that contract while writing and uses it to provide predictable
access from folders, cloud-optimized ZIPs, and partitioned catalogs.

## Bindings

| Language | Install | Role | Docs |
|----------|---------|------|------|
| Python | `pip install taco-eo` | read + write | [README](python/README.md) |
| R | `install.packages("taco", repos = "https://asterisk-labs.r-universe.dev")` | **reader** | [README](r/README.md) |
| Julia | `Pkg.Registry.add(url="https://github.com/asterisk-labs/AsteriskRegistry"); Pkg.add("Taco")` | **reader** | [README](julia/README.md) |
| JavaScript | `npm install @asterisk-labs/taco` | **reader** | [README](javascript/README.md) |

The [TACO viewer](deck/playground/) uses the JavaScript reader with the public
TACO/Rumi fixtures.

## Specification

See the [TACO v3 specification](spec/) for the contract, metadata model, and
container formats.

## License

MIT.

<div align="center">
  <br>
  Made with ♥ by
  <br><br>
  <a href="https://asterisk.coop">
    <img src="https://raw.githubusercontent.com/asterisk-labs/cozip/refs/heads/main/images/asterisk_logo.svg" alt="Asterisk Labs" width="320"/>
  </a>
</div>
