<div align="center">
  <h1>TACO</h1>
  <p>
    <a href="https://pypi.org/project/taco-eo"><img src="https://img.shields.io/pypi/v/taco-eo?label=python&logo=python&logoColor=white&color=3776AB&style=flat-square" alt="Python"/></a>
    <a href="https://asterisk-labs.r-universe.dev/taco"><img src="https://img.shields.io/badge/r--universe-taco-276DC3?logo=r&logoColor=white&style=flat-square" alt="R"/></a>
    <a href="https://github.com/asterisk-labs/AsteriskRegistry"><img src="https://img.shields.io/badge/julia-Taco.jl-9558B2?logo=julia&logoColor=white&style=flat-square" alt="Julia"/></a>
    <a href="https://asterisk.coop/taco/spec"><img src="https://img.shields.io/badge/spec-v3-A8B9CC?style=flat-square" alt="Specification"/></a>
  </p>
</div>

---

Open an Earth observation dataset like a table.

TACO keeps files and tabular metadata together under one sample structure. A
dataset can be a ZIP, a folder, or a partitioned catalog.

## Read

Python

```python
import taco

dataset = taco.open_dataset("dataset.zip")
samples = taco.read(dataset)
```

R

```r
dataset <- taco::open_dataset("dataset.zip")
samples <- taco::read(dataset)
```

Julia

```julia
using Taco

dataset = Taco.open_dataset("dataset.zip")
samples = Taco.read(dataset)
```

Python also provides the writer. See the
[`minimal.py`](python/examples/minimal.py) example.

## Packages

- [Python](python/)
- [R](r/)
- [Julia](julia/)
- [Specification](spec/)

TACO uses [cozip](https://github.com/asterisk-labs/cozip) for its ZIP
container and [cozip_reader](https://github.com/asterisk-labs/cozip_reader)
for reading.

## License

MIT.

<div align="center">
  <br>
  <a href="https://asterisk.coop">
    <img src="spec/assets/asterisk_logo.svg" alt="Asterisk Labs" width="400"/>
  </a>
</div>
