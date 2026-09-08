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
