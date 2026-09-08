# Taco.jl

Julia reader for TACO datasets.

```julia
using Pkg
Pkg.add(url="https://github.com/asterisk-labs/taco", subdir="julia")
```

```julia
using Taco

samples = Taco.read("dataset.zip")
parts = Taco.read(["part-0.zip", "part-1.zip"])
```
