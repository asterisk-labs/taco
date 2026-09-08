# taco

Read TACO datasets in R.

```r
remotes::install_github("asterisk-labs/taco", subdir = "r")
```

```r
dataset <- taco::open_dataset("dataset.zip")
samples <- taco::read(dataset)
```
