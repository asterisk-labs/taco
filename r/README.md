# taco

R reader for TACO datasets.

```r
remotes::install_github("asterisk-labs/taco", subdir = "r")
```

```r
samples <- taco::read("dataset.zip")
parts <- taco::read(c("part-0.zip", "part-1.zip"))
```
