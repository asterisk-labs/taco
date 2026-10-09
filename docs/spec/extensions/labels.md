# Labels Extension

- **Title:** Labels
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/labels/v1.0.0/schema.json`
- **Namespace:** `labels`
- **Scope:** Collection
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](labels/v1.0.0/schema.json) · [Example](labels/examples/COLLECTION.json)

This extension stores the class name of each value in a file, such as a segmentation mask.

## Collection fields

| Field | Type | Description |
| --- | --- | --- |
| `labels:files` | object | For each structure declaration, its values and their class names |

```json
"labels:files": {"mask.tif": {"0": "clear", "1": "thick_cloud", "2": "thin_cloud", "3": "shadow", "255": "unlabeled"}}
```

Every key of `labels:files` MUST be a declaration in `taco:structure`. Codes are non-negative integers written as JSON keys, and names are non-empty strings. Names MUST NOT repeat within a file. Codes need not be contiguous. There is no special ignore value; give it a name such as `unlabeled`.

## Level fields

None.

## Writer

```python
from taco.extensions.collection.labels import Labels

taco.Collection(..., labels=Labels(files={"mask.tif": {0: "clear", 1: "cloud", 255: "unlabeled"}}))
```
