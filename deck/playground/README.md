# TACO viewer

A minimal browser viewer for the public
[`asterisk-labs/taco-api-fixtures`](https://huggingface.co/datasets/asterisk-labs/taco-api-fixtures).

The page uses `@asterisk-labs/taco` to open FOLDER, ZIP, and TACOCAT fixtures.
It plots one point per sample from `stac:centroid` or `istac:centroid`. The
fixture control lists the 20 combinations with sample-level STAC or ISTAC
centroids. ISTAC footprints remain metadata: the map deliberately represents
every sample as its EPSG:4326 centroid. A programmatically requested fixture
without one still falls forward to the next compatible case while preserving
the container topology.

Click a point to follow its metadata from `sample.parquet` to the deepest
metadata Parquet. Payload rows expose the reader-calculated `taco:location`;
every file can copy its location or download its bytes, and Rumi assets can also
be range-read directly.

`Dataset`, next to the fixture control, opens the global collection separately.
It presents the collection summary, coverage, contract graph, payload leaves,
and each metadata schema without dumping the raw `taco:structure` or
`taco:metadata` objects into a point.

Run it from the repository root:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000/deck/playground/`.

Sammy Carlos Romualdo
