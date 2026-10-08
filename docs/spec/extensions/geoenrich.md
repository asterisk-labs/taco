# GeoEnrich Extension

- **Title:** GeoEnrich
- **Identifier:** `https://asterisk.coop/taco/spec/extensions/geoenrich/v1.0.0/schema.json`
- **Namespace:** `geoenrich`
- **Scope:** Sample
- **Maturity:** Pilot
- **Owner:** Asterisk Labs

[JSON Schema](geoenrich/v1.0.0/schema.json) · [Example](geoenrich/examples/COLLECTION.json)

This extension adds environmental, socioeconomic and administrative variables to samples.

## Backends

| Backend | Reads | Requires |
| --- | --- | --- |
| `majortom-index` (default) | The row of the sample's 10 km MajorTOM cell in the public index on Source Cooperative | `majortom:code` at 10 km, from the [MajorTOM extension](majortom.md) |
| `earthengine` | Each product sampled at the centroid | A centroid from the [STAC extension](stac.md), and Earth Engine access |

The writer keeps a local copy of a remote index in the cache and revalidates it on each build. Every code MUST have a row in the index.

## Level fields

Each selected variable is one column. By default every variable is selected.

| Field | Type | Nullable | Description |
| --- | --- | --- | --- |
| `geoenrich:elevation` | `float` | Yes | Elevation in metres (Copernicus GLO-30 DEM) |
| `geoenrich:cisi` | `float` | Yes | Critical Infrastructure Spatial Index, 0 to 1 |
| `geoenrich:precipitation` | `float` | Yes | Mean annual precipitation in mm per year (ERA5, 1979 to 2020) |
| `geoenrich:temperature` | `float` | Yes | Mean annual 2 m air temperature in degrees Celsius (ERA5, 1979 to 2020) |
| `geoenrich:soil_clay` | `float` | Yes | Clay content at 0 cm in percent by weight (OpenLandMap) |
| `geoenrich:soil_sand` | `float` | Yes | Sand content at 0 cm in percent by weight (OpenLandMap) |
| `geoenrich:soil_carbon` | `float` | Yes | Soil organic carbon at 0 cm in g/kg (OpenLandMap) |
| `geoenrich:soil_bulk_density` | `float` | Yes | Fine earth bulk density at 0 cm in kg/m3 (OpenLandMap) |
| `geoenrich:soil_ph` | `float` | Yes | Soil pH in water at 0 cm (OpenLandMap) |
| `geoenrich:gdp` | `float` | Yes | GDP per capita for 2022 in 2017 international dollars, PPP |
| `geoenrich:human_modification` | `float` | Yes | Human modification index for 2020, 0 to 1 (GHM v3) |
| `geoenrich:population` | `float` | Yes | Population density in people per km2, 0 over water and Antarctica (GPW v4.11, 2020) |
| `geoenrich:admin_countries` | `string` | No | Country name, or Ocean/Sea/Lakes |
| `geoenrich:admin_states` | `string` | No | State or province name |
| `geoenrich:admin_districts` | `string` | No | District or county name |

## Collection fields

With the `majortom-index` backend the writer stores where the values came from.

| Field | Type | Description |
| --- | --- | --- |
| `geoenrich:backend` | string | `majortom-index` |
| `geoenrich:index_url` | string | The index the values were joined from |

## Writer

```python
taco.Level(
    "sample",
    stac=taco.extensions.sample.stac.STAC,
    majortom=taco.extensions.sample.majortom.MajorTOM(dist_km=10),
    geoenrich=taco.extensions.sample.geoenrich.GeoEnrich(["elevation", "population"]),
)
```

The `earthengine` backend requires `taco-eo[geoenrich]` and is selected with `backend="earthengine"`.
