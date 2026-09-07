# taco

Python tools for building, reading and validating TACO datasets.

## Examples

```bash
python examples/minimal.py
```

- [minimal.py](examples/minimal.py) writes one file.
- [numpy_minimal.py](examples/numpy_minimal.py) writes image and mask arrays.
- [change_detection.py](examples/change_detection.py) uses nested folders and folder metadata.
- [sequence.py](examples/sequence.py) writes a variable-length image sequence.
- [time_series.py](examples/time_series.py) stores dated observations for each site.
- [geospatial.py](examples/geospatial.py) derives MajorTOM cells and the collection extent from STAC metadata.
- [partitioned.py](examples/partitioned.py) partitions a dataset and opens the resulting TACOCAT.
