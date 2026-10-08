from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import as_file, files
from typing import Any, ClassVar
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.parquet as pq

from ...._cache import cached_download
from ....container.parquet import Encoding
from ....contract.extension import Extension, ExtensionContext
from ....contract.naming import validate_field_name
from ..stac.models import centroid_field, lonlat


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
    resource = files("taco.extensions._builtin.geoenrich").joinpath("data", "admin", f"admin{level}.parquet")
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
class GeoEnrich(Extension):
    """Attach selected environmental variables from an explicit backend."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_namespace__: ClassVar[str | None] = "geoenrich"
    __taco_row_local__: ClassVar[bool] = True
    __taco_complete_level__: ClassVar[bool] = True

    variables: tuple[str, ...]
    backend: str
    scale_m: float
    batch_size: int
    max_concurrency: int
    centroid: str
    code: str
    index_url: str
    _MAJORTOM_INDEX_URL: ClassVar[str] = "https://data.source.coop/major-tom/index/global.parquet"
    _BACKENDS: ClassVar[frozenset[str]] = frozenset({"earthengine", "majortom-index"})
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
        backend: str = "majortom-index",
        scale_m: float = 5120,
        batch_size: int = 250,
        max_concurrency: int = 8,
        centroid: str = "stac:centroid",
        code: str = "majortom:code",
        index_url: str = _MAJORTOM_INDEX_URL,
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
        if backend not in self._BACKENDS:
            raise ValueError(f"backend must be one of {sorted(self._BACKENDS)}, got {backend!r}")
        if not math.isfinite(scale_m) or scale_m <= 0:
            raise ValueError("scale_m must be positive")
        if isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be positive")
        if isinstance(max_concurrency, bool) or max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        validate_field_name(code, context="GeoEnrich code")
        if code.count(":") != 1:
            raise ValueError("code must be a qualified metadata field")
        if not isinstance(index_url, str) or not index_url:
            raise ValueError("index_url must be a non-empty string")
        object.__setattr__(self, "variables", selected)
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "scale_m", float(scale_m))
        object.__setattr__(self, "batch_size", int(batch_size))
        object.__setattr__(self, "max_concurrency", int(max_concurrency))
        object.__setattr__(self, "centroid", centroid_field(centroid))
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "index_url", index_url)

    @property
    def requires(self) -> tuple[str, ...]:
        return (self.code,) if self.backend == "majortom-index" else (self.centroid,)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema(
            pa.field(
                name,
                pa.string() if name.startswith("admin_") else pa.float32(),
                nullable=not name.startswith("admin_"),
                metadata={b"description": self._PRODUCTS[name].description.encode()}
                | (Encoding("dictionary").metadata if name.startswith("admin_") else {}),
            )
            for name in self.variables
        )

    def configuration(self) -> dict[str, Any]:
        configuration: dict[str, Any] = {"variables": list(self.variables), "backend": self.backend}
        if self.backend == "majortom-index":
            configuration.update({"code": self.code, "index_url": self.index_url})
        else:
            configuration.update(
                {
                    "scale_m": self.scale_m,
                    "batch_size": self.batch_size,
                    "max_concurrency": self.max_concurrency,
                }
            )
            if self.centroid != "stac:centroid":
                configuration["centroid"] = self.centroid
        return configuration

    def collection_metadata(self) -> Mapping[str, Any]:
        if self.backend == "majortom-index":
            return {"backend": self.backend, "index_url": self.index_url}
        # Preserve append compatibility with datasets written by TACO <= 0.10.2.
        return {}

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return self.compute({name: context.columns[name] for name in self.requires})

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        if self.backend == "majortom-index":
            return self._compute_majortom_index(columns)
        return self._compute_earthengine(columns)

    def _compute_majortom_index(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        import duckdb
        import numpy as np

        codes = list(columns[self.code])
        if not codes:
            return {name: [] for name in self.variables}
        if any(not isinstance(value, str) or not value for value in codes):
            raise ValueError(f"{self.code} must contain non-empty MajorTOM identifiers")

        requested = pa.table({"taco_index": range(len(codes)), "code": codes})
        selections = ", ".join(f'indexed."geoenrich:{name}" AS "{name}"' for name in self.variables)
        connection = duckdb.connect()
        try:
            # Joining against the remote file reads most of it, so a local copy is kept.
            source = self.index_url
            if urlparse(source).scheme in {"http", "https"}:
                source = str(cached_download(source, "majortom-index"))
            connection.register("requested", requested)
            rows = connection.execute(
                f"""
                SELECT requested.taco_index, indexed.id IS NOT NULL AS matched, {selections}
                FROM requested
                LEFT JOIN read_parquet(?) AS indexed ON indexed.id = requested.code
                ORDER BY requested.taco_index
                """,
                [source],
            ).fetchall()
        except Exception as exc:
            raise RuntimeError(f"GeoEnrich could not read MajorTOM index {self.index_url!r}") from exc
        finally:
            connection.close()

        missing = [codes[index] for index, row in enumerate(rows) if not row[1]]
        if missing:
            preview = ", ".join(repr(value) for value in missing[:5])
            suffix = "" if len(missing) <= 5 else f" (and {len(missing) - 5} more)"
            raise ValueError(f"MajorTOM index has no row for {preview}{suffix}")

        result: dict[str, list[Any]] = {name: [] for name in self.variables}
        for row in rows:
            for position, name in enumerate(self.variables, start=2):
                value = row[position]
                result[name].append(value if name.startswith("admin_") or value is None else float(np.float32(value)))
        return result

    def _compute_earthengine(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        try:
            from importlib import import_module

            import numpy as np

            ee = import_module("ee")
        except ImportError as exc:
            raise ImportError("GeoEnrich requires earthengine-api; install taco-eo[geoenrich]") from exc

        count = len(columns[self.centroid])
        points = [(index, *lonlat(value, field=self.centroid)) for index, value in enumerate(columns[self.centroid])]
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


__all__ = ["GeoEnrich"]
