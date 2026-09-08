# taco

Read and write TACO datasets in Python.

```bash
pip install taco-eo
python examples/minimal.py
```

```python
import taco

samples = taco.read("dataset.zip")
parts = taco.read(["part-0.zip", "part-1.zip"])
```

## Examples

Every example is self-contained, uses synthetic data, and writes its output in
the current directory.

| Example | What it demonstrates |
| --- | --- |
| [`minimal.py`](examples/minimal.py) | Smallest possible single-file dataset |
| [`numpy_minimal.py`](examples/numpy_minimal.py) | NumPy image and mask assets with a train/test split |
| [`change_detection.py`](examples/change_detection.py) | Metadata on `before/` and `after/` folders |
| [`sequence.py`](examples/sequence.py) | Variable-length asset sequences |
| [`time_series.py`](examples/time_series.py) | Per-observation time and cloud metadata |
| [`geospatial.py`](examples/geospatial.py) | Compact STAC metadata and derived MajorTOM cells |
| [`stac_segmentation.py`](examples/stac_segmentation.py) | Rich STAC metadata for regular raster chips, labels, bands, scaling, and statistics |
| [`oceantaco_istac.py`](examples/oceantaco_istac.py) | OceanTACO-inspired ISTAC metadata for irregular SWOT swaths and Argo collocations |
| [`partitioned.py`](examples/partitioned.py) | ZIP partitions and their TACOCAT catalog |
