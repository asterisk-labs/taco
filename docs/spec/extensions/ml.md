# ML Extension

- **Title:** ML
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/ml/v1.0.0/schema.json`
- **Namespace:** `ml`
- **Scope:** Collection
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](ml/v1.0.0/schema.json) · [Example](ml/examples/COLLECTION.json)

This extension says what a model reads and what it predicts. The collection `tasks` names the task; the [Labels](labels.md) and [Instrument](instrument.md) extensions describe the classes and bands of those files.

## Collection fields

| Field | Type | Description |
| --- | --- | --- |
| `ml:inputs` | list | Structure declarations or metadata fields a model reads |
| `ml:targets` | list | Structure declarations or metadata fields a model predicts; at least one |

```json
"ml:inputs": ["s2.tif"],
"ml:targets": ["mask.tif"]
```

An entry with a `:` is a metadata field, such as `scene:category` for scene classification; any other entry is a declaration in `taco:structure`. A metadata field MUST occur at only one level. Every entry MUST exist in the contract, MUST NOT repeat within its list, and MUST NOT be both an input and a target.

## Level fields

None.

## Writer

```python
from taco.extensions.collection.ml import ML

taco.Collection(..., tasks=["segmentation"], ml=ML(inputs=["s2.tif"], targets=["mask.tif"]))
```
