# TACO viewer

Browser viewer for TACO datasets, built on `@asterisk-labs/taco`. It opens the
[`taco-api-fixtures`](https://huggingface.co/datasets/asterisk-labs/taco-api-fixtures)
or any public dataset URL (`?url=` works too), plots sample centroids, and shows
the metadata of a sample down to its files. On a laptop or desktop, the SQL tab
filters the samples on the map with a query over `dataset` or `sample`, run by
DuckDB-WASM. The SQL engine loads while the sample metadata downloads.

Rumi files are read directly from their TACO asset ranges with Rumi's JavaScript
reader, which loads its GeoZL WebAssembly decoder. The preview uses the first
time step and one band or an RGB combination. Images larger than 1024 × 1024
are shown as a centered, georeferenced crop because Rumi does not yet store
overviews. The decoder requires a browser with Memory64 support.
If the data server ignores byte ranges, the playground accepts a complete
response only when the containing object is at most 64 MiB.

```bash
make site
python -m http.server 8000
```

Open `http://localhost:8000/_site/playground/`.
