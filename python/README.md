# taco

Read and write TACO datasets in Python.

```bash
pip install taco-eo
python examples/minimal.py
```

Published wheels include the native TACO reader. Building from the source
distribution requires a C++23 compiler, CMake, Ninja, pkg-config, libcurl
7.83 or newer, and OpenSSL 3 or newer.

```python
import taco

samples = taco.read("dataset.zip")
parts = taco.read(["part-0.zip", "part-1.zip"])

dataset = taco.open_dataset("dataset.zip")
targets = dataset.read(files="target.tif")
train = dataset.sql("SELECT * FROM dataset WHERE \"ml:split\" = 'train'")
```

`export()` writes the complete samples selected by a SQL query. The query may
use `dataset`, `sample`, or any declared metadata level. A match at a lower
level still copies the whole sample. Collection fields are inherited unless
they are replaced. For a remote source, only payloads from matching samples
are downloaded. Pass `overwrite=True` to replace an existing TACO output.

```python
source = "https://data.source.coop/major-tom/core-dem/"
taco.export(
    source,
    "core-dem-sample.zip",
    sql='SELECT * FROM sample ORDER BY id LIMIT 10',
)
```

Remote reads and exports show download progress in interactive terminals.
Writers show their build progress when opened with `progress=True`.

## GeoEnrich

`GeoEnrich` uses the public 10 km MajorTOM index on Source Cooperative by
default, so it needs no Earth Engine account. Place `MajorTOM(dist_km=10)` in
the same metadata level before using it:

```python
taco.Level(
    "sample",
    stac=taco.extensions.STAC(),
    majortom=taco.extensions.MajorTOM(dist_km=10),
    geoenrich=taco.extensions.GeoEnrich(
        ["elevation", "temperature", "admin_countries"],
    ),
)
```

Set `backend="earthengine"` explicitly to retain centroid-based Earth Engine
sampling. That backend requires `taco-eo[geoenrich]` and an authenticated Earth
Engine installation.

## Examples

Every example is self-contained, uses synthetic data, and writes its output in
the current directory.

Spatial and temporal metadata use separate profiles: `Spatial` for regular
spatial grids, `ISpatial` for irregular footprints, and `Temporal` for time
alone. `STAC` combines regular spatial + temporal metadata; `ISTAC` combines
irregular spatial + temporal metadata.

| Example | What it demonstrates |
| --- | --- |
| [`minimal.py`](examples/minimal.py) | Smallest possible single-file dataset |
| [`numpy_minimal.py`](examples/numpy_minimal.py) | NumPy image and mask assets with a train/test split |
| [`change_detection.py`](examples/change_detection.py) | Metadata on `before/` and `after/` folders |
| [`sequence.py`](examples/sequence.py) | Variable-length asset sequences |
| [`time_series.py`](examples/time_series.py) | Per-observation time and cloud metadata |
| [`geospatial.py`](examples/geospatial.py) | Compact STAC metadata and derived MajorTOM cells |
| [`stac_segmentation.py`](examples/stac_segmentation.py) | STAC extensions for regular raster chips, labels, bands, and scaling |
| [`oceantaco_istac.py`](examples/oceantaco_istac.py) | OceanTACO-inspired ISTAC metadata for irregular SWOT swaths and Argo collocations |
| [`partitioned.py`](examples/partitioned.py) | ZIP partitions and their TACOCAT catalog |
