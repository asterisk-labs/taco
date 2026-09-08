# taco

Python package for writing and reading TACO datasets.

```bash
pip install taco-eo
python examples/minimal.py
```

```python
import taco

samples = taco.open("dataset.zip").read()
parts = taco.open(["part-0.zip", "part-1.zip"]).read()
```
