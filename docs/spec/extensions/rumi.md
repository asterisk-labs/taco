# Rumi Extension

- **Title:** Rumi
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/rumi/v1.0.0/schema.json`
- **Namespace:** `rumi`
- **Scope:** Sample
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](rumi/v1.0.0/schema.json) · [Example](rumi/examples/COLLECTION.json)

This extension stores the canonical header and selected statistics of `.rumi` files, so a reader can access them selectively and filter by their values without parsing the payload first.

## Level fields

The extension reads one local `.rumi` file per row. It stores `rumi:header` unless
`header=False`, plus any selected statistics. At least one output MUST be enabled.

| Field | Type | Nullable | Description |
| --- | --- | --- | --- |
| `rumi:header` | `binary` | No | Canonical Rumi header of the file |
| `rumi:<statistic>` | `double` | Yes | One statistic, optionally restricted to a band or time step |

The writer MUST obtain `rumi:header` from `rumi.info(source=asset_path).header`. Producers MUST NOT construct this value themselves.

### Statistics

| Statistic | Value |
| --- | --- |
| `minimum`, `maximum` | Smallest and largest valid value |
| `mean` | Arithmetic mean |
| `stddev` | Population standard deviation |
| `p2`, `p98` | 2nd and 98th percentiles, interpolated linearly between the closest ranks |

Without a selection, a statistic covers every valid value of the array: all bands and, in a Cube, all time steps. `_b<band>` selects one band, `_t<time>` one time step of a Cube, and `_t<time>_b<band>` one band at one time step. Indexes start at zero and have no leading zeros, like variable sequences.

`stats=True` stores the six statistics without a selection. `stats=["mean", "mean_b10", "p98_t0_b3"]` stores exactly those three columns for every Rumi file at the level.

```
rumi:minimum  rumi:maximum  rumi:mean  rumi:stddev  rumi:p2  rumi:p98
```

Statistics exclude non-finite values; booleans count as 0 and 1. The extension has no `nodata` option. Dataset-specific masking belongs outside this extension. A statistic is null when its selection holds no valid value.

### Selection per file

`stats` MAY instead map a complete structure declaration to its own selection. The mapping MUST be non-empty, and each value MUST be `True`, a statistic name, or a non-empty list of statistic names. A declaration omitted from the mapping gets no statistics; it MUST NOT be represented by `False` or an empty list. The mapping is declared at the metadata level that owns those assets. For a variable sequence, the key is the declaration itself, including `*[min,max]`.

```python
taco.extensions.sample.rumi.Rumi(
    stats={
        "rumi/image.rumi": ["mean", "p98"],
        "rumi/cube.rumi": ["mean_t0", "p98_t0_b3"],
    }
)
```

The level stores the union of the selected statistic columns. A row contains null for a statistic that does not apply to its file. In `taco:metadata`, each restricted statistic declares the structure declarations it applies to with `files`.

```json
"rumi:mean": {
  "type": "double",
  "nullable": true,
  "description": "Mean of all valid values",
  "files": ["rumi/image.rumi"]
}
```

## Collection fields

None.

## Contract rules

A contract that declares any other field in the `rumi` namespace, declares `rumi:header` with a type other than `binary`, or declares a Rumi statistic with a type other than `double` is invalid. `files` is a non-empty list of distinct structure declarations at the same metadata level.

The writer MUST reject complex values, an asset that lacks a selected band or time step, and a time selection on an Image.

## File columns

The reader places the Rumi fields beside each `.rumi` file in `read()` and the `dataset` relation. After `{file}::location`, a file has a `{file}::header` column when its level declares `rumi:header`, and one `{file}::<statistic>` column for every statistic that applies to that declaration, such as `{file}::mean_b10`. They follow `taco:metadata` order.

```
read("multisensor.zip")
# taco:sample_index | id       | optical.rumi::location | optical.rumi::header | optical.rumi::mean | radar.rumi::location | ...
# 0            | lima-001 | /vsisubfile/...       | b"LOVE..."          | 1204.5             | /vsisubfile/...     | ...
```

A Rumi variable sequence has a `LIST(BLOB)` column named after its prefix, such as `before/img::header`, and applicable `LIST(DOUBLE)` statistic columns such as `before/img::mean`.

## Writer

```python
taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi(stats=True))
```

`taco.extensions.sample.rumi.Rumi` requires a local `.rumi` asset and the `taco-eo[rumi]` extra.
