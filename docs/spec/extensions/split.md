# Split Extension

- **Title:** Split
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/split/v1.0.0/schema.json`
- **Namespace:** `split`
- **Scope:** Sample
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](split/v1.0.0/schema.json) · [Example](split/examples/COLLECTION.json)

This extension records the partition of each sample, so every tool reads the same training, validation and test sets.

## Level fields

| Field | Type | Nullable | Description |
| --- | --- | --- | --- |
| `split:split` | `string` | No | `train`, `validation`, `test`, or `excluded` |

`split:split` MUST appear at the `sample` level and nowhere else. Every sample MUST have one of the four values, and `split:counts` MUST match the stored rows. An `excluded` sample stays in the dataset but belongs to no partition, for example because it is a duplicate or overlaps another benchmark.

## Collection fields

| Field | Type | Description |
| --- | --- | --- |
| `split:counts` | object | Number of samples in `train`, `validation`, `test` and `excluded`, including the empty ones |

The writer computes `split:counts` from the rows it writes; a producer does not supply it. A TACOCAT adds the counts of its partitions.

```json
"split:counts": {"train": 800, "validation": 100, "test": 95, "excluded": 0}
```

## Writer

```python
from taco.extensions.sample.split import Split

taco.Level("sample", split=Split)
taco.Metadata(split=Split(split="train"))
```

`partition_by="split:split"` writes one ZIP per partition.
