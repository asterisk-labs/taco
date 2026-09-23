<div align="center">
  <img src="docs/images/banner.svg" alt="TACO — AI-ready Earth Observation datasets." width="700"/>
  <p>
    <a href="docs/spec/LICENSE"><img src="https://img.shields.io/badge/license-MIT-EAB308?style=flat-square" alt="License MIT"/></a>
    <a href="https://github.com/asterisk-labs/taco/actions/workflows/release.yml"><img src="https://img.shields.io/github/actions/workflow/status/asterisk-labs/taco/release.yml?label=release&style=flat-square" alt="Release"/></a>
    <a href="https://github.com/asterisk-labs/taco/actions/workflows/python.yml"><img src="https://img.shields.io/badge/python%20coverage-88%25-brightgreen?style=flat-square" alt="Python coverage 88%"/></a>
    <a href="https://pypi.org/project/taco-eo"><img src="https://img.shields.io/pypi/v/taco-eo?label=python&logo=python&logoColor=white&color=3776AB&style=flat-square" alt="Python"/></a>
    <a href="https://pypi.org/project/taco-eo"><img src="https://img.shields.io/pypi/pyversions/taco-eo?style=flat-square" alt="Supported Python versions"/></a>
    <a href="https://asterisk-labs.r-universe.dev/taco"><img src="https://img.shields.io/badge/r--universe-taco-276DC3?logo=r&logoColor=white&style=flat-square" alt="R"/></a>
    <a href="https://github.com/asterisk-labs/AsteriskRegistry"><img src="https://img.shields.io/badge/julia-Taco.jl-9558B2?logo=julia&logoColor=white&style=flat-square" alt="Julia"/></a>
    <a href="javascript/"><img src="https://img.shields.io/badge/javascript-v0.9.0-F7DF1E?logo=javascript&logoColor=black&style=flat-square" alt="JavaScript 0.9.0"/></a>
    <a href="https://asterisk.coop/taco/spec"><img src="https://img.shields.io/badge/spec-v3-A8B9CC?style=flat-square" alt="Specification"/></a>
  </p>
</div>

---

TACO packages Earth observation data for machine learning. It treats each
training or evaluation example as a sample, an atomic unit that keeps its files
and metadata together.

A contract defines the structure shared by all samples, allowing TACO to
validate the dataset as it is built and filter it by region, time, cloud cover,
split, or any other field before reading the files. TACO is agnostic to deep
learning frameworks, programming languages, and operating systems.

## Bindings

| Language | Install | Role | Docs |
|----------|---------|------|------|
| Python | `pip install taco-eo` | read + write | [README](python/README.md) |
| R | `install.packages("taco", repos = "https://asterisk-labs.r-universe.dev")` | **reader** | [README](r/README.md) |
| Julia | `Pkg.Registry.add(url="https://github.com/asterisk-labs/AsteriskRegistry"); Pkg.add("Taco")` | **reader** | [README](julia/README.md) |
| JavaScript | `npm install @asterisk-labs/taco` | **reader** | [README](javascript/README.md) |

The [TACO viewer](docs/playground/) uses the JavaScript reader to inspect TACO
datasets directly in the browser.

## Specification

See the [TACO v3 specification](docs/spec/SPEC.md) for the contract, metadata model, and
container formats.

Release history for the core and all language packages is recorded in the
[changelog](CHANGELOG.md).

## License

MIT.

## Partners

The groups below build datasets with TACO. What they need from it is what
shapes the specification.

| Institution | Group | Contact |
|-------------|-------|---------|
| [Asterisk Labs](https://asterisk.coop) | | Cesar Aybar |
| [Universitat de València](https://isp.uv.es) | Image and Signal Processing | Luis Gómez-Chova |
| [University of Salamanca](https://www.usal.es) | TIDOP Research Group | Roy Yali Samaniego |
| [Leipzig University](https://www.uni-leipzig.de) | Remote sensing and Earth system data | David Montero |
| [Technical University of Munich](https://www.tum.de) | Munich Center for Machine Learning | Nils Lehmann |
| [ELLIOT](https://elliot-ai.eu) | European open multimodal foundation models | Oscar José Pellicer Valero |

<div align="center">
  <br>
  <!-- Heights are tuned per logo so every mark covers a similar area: a wide
       wordmark and a square badge look unbalanced at one shared height. -->
  <a href="https://asterisk.coop"><img src="https://raw.githubusercontent.com/asterisk-labs/cozip/refs/heads/main/images/asterisk_logo.svg" alt="Asterisk Labs" height="36"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://isp.uv.es"><img src="docs/images/partners/uv.svg" alt="Universitat de València" height="54"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://tidop.usal.es"><img src="docs/images/partners/tidop.svg" alt="TIDOP Research Group at the University of Salamanca" height="54"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://www.uni-leipzig.de"><img src="docs/images/partners/leipzig.svg" alt="Leipzig University" height="56"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://www.tum.de"><img src="docs/images/partners/tum.svg" alt="Technical University of Munich" height="44"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://elliot-ai.eu"><img src="docs/images/partners/elliot.webp" alt="ELLIOT" height="54"/></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://www.climatechange.ai/innovation_grants"><img src="docs/images/partners/ccai.png" alt="Climate Change AI" height="60"/></a>
</div>
