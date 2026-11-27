# Taco.jl

Read TACO datasets in Julia.

```julia
using Pkg
Pkg.add(url="https://github.com/asterisk-labs/taco", subdir="julia")
```

Taco.jl downloads the native library listed in `Artifacts.toml` on first use.
A development build can be selected with the `TACO_LIB` environment variable.

When using this source checkout, run `make core` before calling `read_bytes`;
the published native artifact may predate that function.

```julia
using Taco

dataset = Taco.open_dataset("dataset.zip")
samples = Taco.read(dataset)
first = Taco.read_bytes(samples[1, "target.tif::location"])
```
