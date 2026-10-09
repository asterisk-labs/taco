# Instrument Extension

- **Title:** Instrument
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/instrument/v1.0.0/schema.json`
- **Namespace:** `instrument`
- **Scope:** Collection
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](instrument/v1.0.0/schema.json) · [Example](instrument/examples/COLLECTION.json)

This extension records the instruments that may produce each file and lists its bands
in file order. For known instruments, the writer embeds metadata from
[Awesome Earth Observation Instruments](https://github.com/awesome-spectral-indices/awesome-earth-observation-instruments)
(AEOI). TACO ships a copy under its MIT license.

## Collection fields

| Field | Type | Description |
| --- | --- | --- |
| `instrument:files` | object | Instruments and ordered bands for each structure declaration |
| `instrument:instruments` | object | Metadata for every referenced instrument, limited to the bands in use |
| `instrument:catalogue` | object | Name, version and link of the bundled AEOI catalogue |

```json
{
  "instrument:files": {
    "s2.tif": {
      "instruments": ["MSI_S2A"],
      "bands": ["B4"]
    }
  },
  "instrument:instruments": {
    "MSI_S2A": {
      "name": "MultiSpectral Instrument",
      "type": "multispectral",
      "platform": ["Sentinel-2A"],
      "bands": {
        "B4": {
          "center_wavelength": 664.6,
          "bandwidth": 30,
          "common_name": "red",
          "gsd": 10
        }
      }
    }
  },
  "instrument:catalogue": {
    "name": "Awesome Earth Observation Instruments",
    "version": "0.4.0",
    "link": "https://github.com/awesome-spectral-indices/awesome-earth-observation-instruments/raw/refs/heads/main/catalogue/catalogue.json"
  }
}
```

A declaration may name several compatible instruments. For example, the same band
stack may come from Sentinel-2A or Sentinel-2B. Every listed instrument MUST provide
every listed band. This does not describe a file assembled from instruments with
different band sets.

Each key in `instrument:files` MUST match a declaration in `taco:structure`. The writer
resolves known identifiers from AEOI, copies only the bands in use and records the
catalogue version. A custom instrument needs a non-empty `name`, `type` and `bands`.
Each band needs a positive `center_wavelength` and `bandwidth`, both in nm.

## Level fields

None.

## Writer

```python
from taco.extensions.collection.instrument import Instrument

taco.Collection(
    ...,
    instrument=Instrument(
        files={
            "s2.tif": {"instruments": ["MSI_S2A", "MSI_S2B"], "bands": ["B2", "B3", "B4", "B8"]},
            "cam.tif": {"instruments": ["MY_CAMERA"], "bands": ["B1"]},
        },
        instruments={
            "MY_CAMERA": {
                "name": "My camera",
                "type": "multispectral",
                "bands": {"B1": {"center_wavelength": 560, "bandwidth": 40}},
            }
        },
    ),
)
```

Use `instruments` only for identifiers missing from AEOI. Pass an `Instrument` object,
not a plain mapping, so the writer can resolve the catalogue records. Writing and
validation work offline.
