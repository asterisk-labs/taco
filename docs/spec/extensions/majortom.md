# MajorTOM Extension

- **Title:** MajorTOM
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/majortom/v1.0.0/schema.json`
- **Namespace:** `majortom`
- **Scope:** Sample
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](majortom/v1.0.0/schema.json) · [Example](majortom/examples/COLLECTION.json)

This extension assigns each sample the cell of the spherical [MajorTOM](https://github.com/ESA-PhiLab/Major-TOM) grid that contains its centroid. The code joins a sample to other MajorTOM datasets and indexes.

## Dependencies

A centroid from the [STAC extension](stac.md), normally `stac:centroid` or `spatial:centroid`.

## Level fields

| Field | Type | Nullable | Description |
| --- | --- | --- | --- |
| `majortom:code` | `string` | No | Cell at `dist_km` kilometres |
| `majortom:<name>` | `string` | No | Cell of one extra grid |

A code joins the grid size, the row and the column with `sep`, such as `MT100km_0012U_0034R`. Rows count up (`U`) and down (`D`) from the equator, and columns right (`R`) and left (`L`) from the prime meridian, with four digits.

## Collection fields

Every parameter changes what a code means, so the writer stores all of them.

| Field | Type | Description |
| --- | --- | --- |
| `majortom:dist_km` | number | Positive cell size of `majortom:code`, in kilometres |
| `majortom:extra` | object | Name and positive cell size of each extra grid. A name MUST NOT be `code` or contain `:` |
| `majortom:latitude_range` | array | Increasing `[min, max]` latitude covered by the grid |
| `majortom:longitude_range` | array | Increasing `[min, max]` longitude covered by the grid |
| `majortom:sep` | string | Non-empty separator with no letters or digits |
| `majortom:centroid` | string | The centroid field the codes were computed from |

An append whose parameters differ from the stored ones MUST fail. For example, `MajorTOM(dist_km=50)` cannot extend a dataset built with `dist_km=100`, because one column would hold codes from two grid sizes.

## Writer

```python
taco.Level(
    "sample",
    stac=taco.extensions.sample.stac.STAC,
    majortom=taco.extensions.sample.majortom.MajorTOM(dist_km=100, extra={"code_10km": 10}),
)
```
