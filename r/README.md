# taco

Read TACO datasets in R.

```r
remotes::install_github("asterisk-labs/taco", subdir = "r")
```

The source package carries the TACO and Karu sources and compiles them during
installation. It requires a C++23 compiler, pkg-config, libcurl 7.83 or newer,
and OpenSSL 3 or newer.

The R package targets native R installations. WebAssembly builds are not
supported because the reader core depends on DuckDB, libcurl, and OpenSSL.

```r
dataset <- taco::open_dataset("dataset.zip")
samples <- taco::read(dataset)
first <- taco::read_bytes(samples[["target.tif::location"]][[1]])
```
