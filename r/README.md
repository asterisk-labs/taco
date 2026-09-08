# taco

R reader for TACO datasets.

```r
remotes::install_github("asterisk-labs/taco", subdir = "r")
```

```r
dataset <- taco::open_dataset("dataset.zip")
samples <- taco::read(dataset)
```
