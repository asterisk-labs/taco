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

`export()` writes a smaller dataset with the same contract. `samples` is a
PyArrow-compatible table, normally selected from the `dataset` SQL relation.
Keyword arguments replace fields of the collection, such
as `id` or `description`; the rest is inherited. For a remote source, metadata
is cached and only the payload files belonging to the selected samples are
downloaded. Pass `overwrite=True` to replace an existing TACO output.

```python
source = "https://data.source.coop/major-tom/core-dem/"
dataset = taco.open_dataset(source)
rows = dataset.sql('SELECT * FROM dataset ORDER BY "taco:sample_index" LIMIT 10')
taco.export(
    source,
    "core-dem-sample.zip",
    samples=rows,
    id="core-dem-sample",
    description="Ten samples from Core-DEM",
)
```

Remote reads and exports show download progress in interactive terminals.
Writers show their build progress when opened with `progress=True`.

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
