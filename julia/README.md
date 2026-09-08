# Taco.jl

Julia reader for TACO datasets.

```julia
using Pkg
Pkg.add(url="https://github.com/asterisk-labs/taco", subdir="julia")
```

```julia
using Taco

dataset = Taco.open_dataset("dataset.zip")
samples = Taco.read(dataset)
```
