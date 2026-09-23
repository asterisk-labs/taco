from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import as_file, files
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.parquet as pq

from ..contract.naming import validate_field_name
from ._base import DerivedMetadata
from .spatiotemporal import point_from_wkb


def _centroid_field(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_]*:centroid", value) is None:
        raise ValueError("centroid must be a qualified metadata field ending in ':centroid'")
    return value


def _distance_label(value: float) -> str:
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


@lru_cache(maxsize=32)
def _grid(
    dist_km: float,
    latitude_range: tuple[float, float],
    longitude_range: tuple[float, float],
) -> tuple[Any, Any, tuple[Any, ...], tuple[Any, ...]]:
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("MajorTOM requires numpy") from exc

    divisions = math.ceil(math.pi * 6378.137 / dist_km)
    all_lats = np.linspace(-90, 90, divisions + 1)[:-1]
    all_lats = np.sort(np.mod(all_lats, 180) - 90)
    zero = int(np.searchsorted(all_lats, 0, side="left"))
    all_rows = np.empty(all_lats.size, dtype=object)
    all_rows[zero:] = [f"{index:04d}U" for index in range(all_lats.size - zero)]
    all_rows[:zero] = [f"{zero - index:04d}D" for index in range(zero)]
    mask = (all_lats >= latitude_range[0]) & (all_lats <= latitude_range[1])
    lats = all_lats[mask]
    rows = all_rows[mask]
    if not lats.size:
        raise ValueError("latitude_range does not contain a MajorTOM grid row")

    longitude_rows = []
    labels = []
    for lat in lats:
        circumference = 2 * math.pi * 6378.137 * math.cos(math.radians(float(lat)))
        count = max(1, math.ceil(circumference / dist_km))
        all_lons = np.sort(np.mod(np.linspace(-180, 180, count + 1)[:-1], 360) - 180)
        zero_hits = np.flatnonzero(all_lons == 0)
        center = int(zero_hits[0]) if zero_hits.size else int(np.argmin(np.abs(all_lons)))
        all_labels = np.empty(all_lons.size, dtype=object)
        all_labels[center:] = [f"{index:04d}R" for index in range(all_lons.size - center)]
        all_labels[:center] = [f"{center - index:04d}L" for index in range(center)]
        mask = (all_lons >= longitude_range[0]) & (all_lons <= longitude_range[1])
        longitude_rows.append(all_lons[mask])
        labels.append(all_labels[mask])
    return lats, rows, tuple(longitude_rows), tuple(labels)


@dataclass(frozen=True)
class MajorTOM(DerivedMetadata):
    """Assign the spherical MajorTOM grid cell containing each centroid."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    dist_km: float = 100
    extra: Mapping[str, float] | tuple[tuple[str, float], ...] = ()
    latitude_range: tuple[float, float] = (-85.0, 85.0)
    longitude_range: tuple[float, float] = (-180.0, 180.0)
    sep: str = "_"
    centroid: str = "stac:centroid"

    def __post_init__(self) -> None:
        if not math.isfinite(self.dist_km) or self.dist_km <= 0:
            raise ValueError("dist_km must be positive")
        if len(self.latitude_range) != 2 or self.latitude_range[0] >= self.latitude_range[1]:
            raise ValueError("latitude_range must be an increasing pair")
        if len(self.longitude_range) != 2 or self.longitude_range[0] >= self.longitude_range[1]:
            raise ValueError("longitude_range must be an increasing pair")
        if not self.sep or any(char.isalnum() for char in self.sep):
            raise ValueError("sep must contain only separator characters")
        object.__setattr__(self, "dist_km", float(self.dist_km))
        object.__setattr__(self, "latitude_range", tuple(float(value) for value in self.latitude_range))
        object.__setattr__(self, "longitude_range", tuple(float(value) for value in self.longitude_range))
        object.__setattr__(self, "centroid", _centroid_field(self.centroid))
        object.__setattr__(self, "extra", self._normalized_extra())

    def _normalized_extra(self) -> tuple[tuple[str, float], ...]:
        """Validate the extra grids and freeze them so the class stays hashable."""
        try:
            items = dict(self.extra).items()
        except (TypeError, ValueError) as exc:
            raise TypeError("extra must be a mapping of grid names to distances") from exc
        normalized = []
        for name, distance in items:
            validate_field_name(name, context="MajorTOM extra")
            if ":" in name:
                raise ValueError(f"extra grid {name!r} must not contain ':'")
            if name == "code":
                raise ValueError("an extra grid cannot be named 'code'")
            if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                raise ValueError(f"extra grid {name!r} needs a positive distance in kilometres")
            if not math.isfinite(distance) or distance <= 0:
                raise ValueError(f"extra grid {name!r} needs a positive distance in kilometres")
            normalized.append((name, float(distance)))
        return tuple(normalized)

    def _grids(self) -> tuple[tuple[str, float], ...]:
        """The primary grid first, then the extra ones."""
        return (("code", self.dist_km), *self.extra)

    @property
    def requires(self) -> tuple[str, ...]:
        return (self.centroid,)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field(
                    name,
                    pa.string(),
                    nullable=False,
                    metadata={
                        b"description": (
                            f"MajorTOM spherical grid cell identifier at {_distance_label(distance)} km"
                        ).encode()
                    },
                )
                for name, distance in self._grids()
            ]
        )

    def configuration(self) -> dict[str, Any]:
        configuration = {
            "dist_km": self.dist_km,
            "latitude_range": list(self.latitude_range),
            "longitude_range": list(self.longitude_range),
            "sep": self.sep,
        }
        if self.extra:
            configuration["extra"] = dict(self.extra)
        if self.centroid != "stac:centroid":
            configuration["centroid"] = self.centroid
        return configuration

    def collection_metadata(self) -> Mapping[str, Any]:
        return {
            "dist_km": self.dist_km,
            "extra": dict(self.extra),
            "latitude_range": list(self.latitude_range),
            "longitude_range": list(self.longitude_range),
            "sep": self.sep,
            "centroid": self.centroid,
        }

    def _codes(self, longitudes: Any, latitudes: Any, dist_km: float) -> list[str]:
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError("MajorTOM requires numpy") from exc

        lats, row_labels, longitude_rows, column_labels = _grid(dist_km, self.latitude_range, self.longitude_range)
        row_indexes = np.searchsorted(lats, latitudes, side="left") - 1
        row_indexes = np.clip(row_indexes, 0, len(lats) - 1)
        column_indexes: Any = np.empty_like(row_indexes)
        for row_index in np.unique(row_indexes):
            selected = row_indexes == row_index
            row_lons = longitude_rows[int(row_index)]
            if not row_lons.size:
                raise ValueError("longitude_range does not contain a MajorTOM grid column")
            indexes: Any = np.searchsorted(row_lons, longitudes[selected], side="left") - 1
            indexes[indexes < 0] = row_lons.size - 1
            column_indexes[selected] = indexes

        distance = f"MT{_distance_label(dist_km)}km"
        return [
            self.sep.join(
                (
                    distance,
                    str(row_labels[int(row_index)]),
                    str(column_labels[int(row_index)][int(column_index)]),
                )
            )
            for row_index, column_index in zip(row_indexes, column_indexes, strict=True)
        ]

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError("MajorTOM requires numpy") from exc

        points = [point_from_wkb(value, field=self.centroid) for value in columns[self.centroid]]
        longitudes = np.asarray([point[0] for point in points])
        latitudes = np.asarray([point[1] for point in points])
        return {name: self._codes(longitudes, latitudes, distance) for name, distance in self._grids()}


def _morton_key(longitude: float, latitude: float, bits: int = 24) -> int:
    maximum = (1 << bits) - 1
    x = max(0, min(maximum, int((longitude + 180.0) / 360.0 * maximum)))
    y = max(0, min(maximum, int((latitude + 90.0) / 180.0 * maximum)))

    def spread(value: int) -> int:
        value &= 0xFFFFFFFF
        value = (value | value << 16) & 0x0000FFFF0000FFFF
        value = (value | value << 8) & 0x00FF00FF00FF00FF
        value = (value | value << 4) & 0x0F0F0F0F0F0F0F0F
        value = (value | value << 2) & 0x3333333333333333
        return (value | value << 1) & 0x5555555555555555

    return spread(x) << 1 | spread(y)


@lru_cache(maxsize=3)
def _admin_names(level: int) -> dict[int, str]:
    resource = files("taco").joinpath("metadata", "data", "admin", f"admin{level}.parquet")
    with as_file(resource) as path:
        table = pq.read_table(path, columns=[f"admin_code{level}", "name"])
    codes, names = table.columns
    return dict(zip(codes.to_pylist(), names.to_pylist(), strict=True))


_OCEAN_CODE = 65535


@dataclass(frozen=True)
class _Product:
    asset: str
    description: str
    band: str | None = None
    mosaic: bool = False
    reducer: str = "mean"
    scale: float = 1.0
    offset: float = 0.0
    fill: float | None = None

    def convert(self, value: Any) -> float | None:
        if value is None:
            return None
        return float(value) * self.scale + self.offset


def _get_info(request: Any) -> Any:
    for delay in (1, 2, 4, 8):
        try:
            return request.getInfo()
        except Exception:
            time.sleep(delay)
    return request.getInfo()


@dataclass(frozen=True, init=False)
class GeoEnrich(DerivedMetadata):
    """Fetch selected Earth Engine variables and resolve administrative names."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_complete_level__: ClassVar[bool] = True

    variables: tuple[str, ...]
    scale_m: float
    batch_size: int
    max_concurrency: int
    centroid: str
    _PRODUCTS: ClassVar[dict[str, _Product]] = {
        "elevation": _Product(
            "projects/sat-io/open-datasets/GLO-30",
            "Elevation in metres (Copernicus GLO-30 DEM)",
            mosaic=True,
        ),
        "cisi": _Product(
            "projects/sat-io/open-datasets/CISI/global_CISI",
            "Critical Infrastructure Spatial Index, 0 to 1 (Nirandjan et al. 2022)",
        ),
        # ERA5 monthly climatologies for 1979-01 to 2020-06, precomputed once and stored in metres and kelvin.
        "precipitation": _Product(
            "projects/ee-csaybar-real/assets/precipitation",
            "Mean annual precipitation in mm per year (ERA5, 1979 to 2020)",
            scale=1000.0,
        ),
        "temperature": _Product(
            "projects/ee-csaybar-real/assets/temperature",
            "Mean annual 2 m air temperature in degrees Celsius (ERA5, 1979 to 2020)",
            offset=-273.15,
        ),
        # OpenLandMap stores scaled integers; the factors are gee:scale in the catalog.
        "soil_clay": _Product(
            "OpenLandMap/SOL/SOL_CLAY-WFRACTION_USDA-3A1A1A_M/v02",
            "Clay content at 0 cm depth in percent by weight (OpenLandMap)",
            band="b0",
        ),
        "soil_sand": _Product(
            "OpenLandMap/SOL/SOL_SAND-WFRACTION_USDA-3A1A1A_M/v02",
            "Sand content at 0 cm depth in percent by weight (OpenLandMap)",
            band="b0",
        ),
        "soil_carbon": _Product(
            "OpenLandMap/SOL/SOL_ORGANIC-CARBON_USDA-6A1C_M/v02",
            "Soil organic carbon at 0 cm depth in g/kg (OpenLandMap)",
            band="b0",
            scale=5.0,
        ),
        "soil_bulk_density": _Product(
            "OpenLandMap/SOL/SOL_BULKDENS-FINEEARTH_USDA-4A1H_M/v02",
            "Fine earth bulk density at 0 cm depth in kg/m3 (OpenLandMap)",
            band="b0",
            scale=10.0,
        ),
        "soil_ph": _Product(
            "OpenLandMap/SOL/SOL_PH-H2O_USDA-4C1A2A_M/v02",
            "Soil pH in water at 0 cm depth (OpenLandMap)",
            band="b0",
            scale=0.1,
        ),
        "gdp": _Product(
            "projects/sat-io/open-datasets/GRIDDED_HDI_GDP/total_gdp_perCapita_1990_2022_5arcmin",
            "Total GDP for 2022 in 2017 international dollars, PPP (Kummu et al. 2025)",
            band="PPP_2022",
        ),
        "human_modification": _Product(
            "projects/sat-io/open-datasets/GHM/HM_1990_2020_OVERALL_300M/HMv20240801_2020c_AA_300",
            "Human modification index for 2020, 0 to 1 (GHM v3, Theobald et al. 2025)",
            band="constant",
        ),
        # GPW masks water and Antarctica. Nobody lives there, so masked pixels become 0 rather than null.
        "population": _Product(
            "CIESIN/GPWv411/GPW_Population_Density/gpw_v4_population_density_rev11_2020_30_sec",
            "Population density in people per km2, 0 over water and Antarctica (GPW v4.11, 2020)",
            band="population_density",
            fill=0.0,
        ),
        "admin_countries": _Product(
            "projects/ee-csaybar-real/assets/admin0",
            "Country name at the centroid, or Ocean/Sea/Lakes",
            reducer="mode",
            fill=_OCEAN_CODE,
        ),
        "admin_states": _Product(
            "projects/ee-csaybar-real/assets/admin1",
            "State or province name at the centroid",
            reducer="mode",
            fill=_OCEAN_CODE,
        ),
        "admin_districts": _Product(
            "projects/ee-csaybar-real/assets/admin2",
            "District or county name at the centroid",
            reducer="mode",
            fill=_OCEAN_CODE,
        ),
    }

    def __init__(
        self,
        variables: tuple[str, ...] | list[str] | None = None,
        *,
        scale_m: float = 5120,
        batch_size: int = 250,
        max_concurrency: int = 8,
        centroid: str = "stac:centroid",
    ) -> None:
        if isinstance(variables, (str, bytes)):
            raise TypeError("variables must be a sequence of names")
        selected = tuple(self._PRODUCTS if variables is None else variables)
        if not selected:
            raise ValueError("variables must not be empty")
        if len(selected) != len(set(selected)):
            raise ValueError("variables must be unique")
        unknown = sorted(set(selected) - set(self._PRODUCTS))
        if unknown:
            raise ValueError(f"unknown GeoEnrich variables: {unknown}")
        if not math.isfinite(scale_m) or scale_m <= 0:
            raise ValueError("scale_m must be positive")
        if isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be positive")
        if isinstance(max_concurrency, bool) or max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        object.__setattr__(self, "variables", selected)
        object.__setattr__(self, "scale_m", float(scale_m))
        object.__setattr__(self, "batch_size", int(batch_size))
        object.__setattr__(self, "max_concurrency", int(max_concurrency))
        object.__setattr__(self, "centroid", _centroid_field(centroid))

    @property
    def requires(self) -> tuple[str, ...]:
        return (self.centroid,)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema(
            pa.field(
                name,
                pa.string() if name.startswith("admin_") else pa.float32(),
                nullable=not name.startswith("admin_"),
                metadata={b"description": self._PRODUCTS[name].description.encode()},
            )
            for name in self.variables
        )

    def configuration(self) -> dict[str, Any]:
        configuration = {
            "variables": list(self.variables),
            "scale_m": self.scale_m,
            "batch_size": self.batch_size,
            "max_concurrency": self.max_concurrency,
        }
        if self.centroid != "stac:centroid":
            configuration["centroid"] = self.centroid
        return configuration

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        try:
            from importlib import import_module

            import numpy as np

            ee = import_module("ee")
        except ImportError as exc:
            raise ImportError("GeoEnrich requires earthengine-api; install taco-eo[geoenrich]") from exc

        count = len(columns[self.centroid])
        points = [
            (index, *point_from_wkb(value, field=self.centroid)) for index, value in enumerate(columns[self.centroid])
        ]
        points.sort(key=lambda point: _morton_key(point[1], point[2]))
        groups: dict[str, list[tuple[str, Any]]] = {"mean": [], "mode": []}
        for name in self.variables:
            product = self._PRODUCTS[name]
            image = ee.ImageCollection(product.asset).mosaic() if product.mosaic else ee.Image(product.asset)
            if product.fill is not None:
                image = image.unmask(product.fill)
            if product.band is not None:
                image = image.select(product.band)
            groups[product.reducer].append((name, image.rename(name)))
        groups = {name: products for name, products in groups.items() if products}
        chunks = [points[start : start + self.batch_size] for start in range(0, count, self.batch_size)]
        result: dict[str, list[Any]] = {
            name: (["Ocean/Sea/Lakes"] * count if name.startswith("admin_") else [None] * count)
            for name in self.variables
        }

        def fetch(chunk: list[tuple[int, float, float]]) -> list[tuple[int, dict[str, Any]]]:
            features = [ee.Feature(ee.Geometry.Point(lon, lat), {"taco_index": index}) for index, lon, lat in chunk]
            collection = ee.FeatureCollection(features)
            rows: dict[int, dict[str, Any]] = {index: {} for index, _, _ in chunk}
            for reducer, products in groups.items():
                image = ee.Image([product[1] for product in products])
                operation = ee.Reducer.mean() if reducer == "mean" else ee.Reducer.mode()
                # A fixed grid keeps each value independent of which other variables are requested.
                response = _get_info(
                    image.reduceRegions(collection=collection, reducer=operation, scale=self.scale_m, crs="EPSG:4326")
                )
                for feature in response.get("features", []):
                    properties = feature.get("properties", {})
                    index = properties.get("taco_index")
                    if not isinstance(index, int):
                        continue
                    for position, (name, _) in enumerate(products):
                        fallback = reducer if position == 0 else f"{reducer}_{position}"
                        rows[index][name] = properties.get(name, properties.get(fallback))
            return list(rows.items())

        with ThreadPoolExecutor(max_workers=min(self.max_concurrency, max(1, len(chunks)))) as executor:
            for chunk_rows in executor.map(fetch, chunks):
                for index, values in chunk_rows:
                    for name, value in values.items():
                        if name.startswith("admin_"):
                            level = {"admin_countries": 0, "admin_states": 1, "admin_districts": 2}[name]
                            result[name][index] = (
                                "Ocean/Sea/Lakes" if value is None else _admin_names(level).get(int(value)) or "Unknown"
                            )
                        else:
                            converted = self._PRODUCTS[name].convert(value)
                            result[name][index] = None if converted is None else float(np.float32(converted))
        return result


__all__ = ["GeoEnrich", "MajorTOM"]
