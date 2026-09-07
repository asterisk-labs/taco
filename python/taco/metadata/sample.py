from __future__ import annotations

import math
import struct
import tempfile
from array import array
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, ClassVar, Literal

import pyarrow as pa
from pydantic import Field, model_validator

from ..contract.collection import Extent
from ._base import CollectionSummary, DerivedMetadata, ScopedModel

TimestampUTC = Annotated[datetime, pa.timestamp("us", tz="UTC")]


def _point(wkb: bytes) -> tuple[float, float]:
    if len(wkb) < 21 or wkb[0] not in (0, 1):
        raise ValueError("stac:centroid must be a WKB point")
    order = "<" if wkb[0] == 1 else ">"
    geometry_type = struct.unpack_from(order + "I", wkb, 1)[0]
    offset = 5
    if geometry_type & 0x20000000:
        offset += 4
    if geometry_type & 0xFF not in (1,):
        raise ValueError("stac:centroid must be a WKB point")
    if len(wkb) < offset + 16:
        raise ValueError("stac:centroid contains incomplete WKB")
    lon, lat = struct.unpack_from(order + "dd", wkb, offset)
    if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180) or not (-90 <= lat <= 90):
        raise ValueError("stac:centroid is outside EPSG:4326 bounds")
    return lon, lat


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


class _STACExtent(CollectionSummary):
    field = "extent"
    requires = ("centroid", "time_start", "time_end")

    def __init__(self) -> None:
        self._longitudes = tempfile.TemporaryFile()  # noqa: SIM115
        self._longitude_count = 0
        self._south = 90.0
        self._north = -90.0
        self._start: datetime | None = None
        self._end: datetime | None = None
        self._result: dict[str, Any] | None = None
        self._finished = False

    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        longitudes = array("d")
        for value in columns["centroid"]:
            if value is None:
                continue
            lon, lat = _point(value)
            longitudes.append(-180 if lon == 180 else lon)
            self._south = min(self._south, lat)
            self._north = max(self._north, lat)
        longitudes.tofile(self._longitudes)
        self._longitude_count += len(longitudes)

        for start, end in zip(columns["time_start"], columns["time_end"], strict=True):
            if start is None:
                continue
            start = _utc(start)
            end = start if end is None else _utc(end)
            self._start = start if self._start is None else min(self._start, start)
            self._end = end if self._end is None else max(self._end, end)

    def finish(self) -> dict[str, Any] | None:
        if self._finished:
            return self._result
        self._finished = True
        if not self._longitude_count:
            self.close()
            return None
        import numpy as np

        self._longitudes.flush()
        longitudes = np.memmap(self._longitudes, dtype=np.float64, mode="r+", shape=(self._longitude_count,))
        longitudes.sort()
        largest = float(longitudes[0] + 360 - longitudes[-1])
        gap = self._longitude_count - 1
        for start in range(0, self._longitude_count - 1, 1_000_000):
            values = longitudes[start : min(self._longitude_count, start + 1_000_001)]
            differences = np.diff(values)
            index = int(np.argmax(differences))
            if differences[index] > largest:
                largest = float(differences[index])
                gap = start + index
        west = float(longitudes[(gap + 1) % self._longitude_count])
        east = float(longitudes[gap])
        temporal = None if self._start is None or self._end is None else (_iso(self._start), _iso(self._end))
        self._result = Extent((west, self._south, east, self._north), temporal).to_dict()
        del longitudes
        self.close()
        return self._result

    def close(self) -> None:
        self._longitudes.close()


class STAC(ScopedModel):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_summaries__ = (_STACExtent,)

    crs: str = Field(description="Coordinate reference system")
    geometry: bytes = Field(description="Spatial footprint as WKB")
    centroid: bytes = Field(description="Centroid in EPSG:4326 as WKB")
    time_start: TimestampUTC = Field(description="Acquisition start")
    time_end: TimestampUTC | None = Field(default=None, description="Acquisition end")

    @model_validator(mode="after")
    def _time_order(self) -> STAC:
        if self.time_end is not None and self.time_start > self.time_end:
            raise ValueError("time_start must not be after time_end")
        return self


class ISTAC(STAC):
    pass


class Split(ScopedModel):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    split: Literal["train", "test", "validation"] = Field(description="Dataset split")


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
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    dist_km: float = 100
    latitude_range: tuple[float, float] = (-85.0, 85.0)
    longitude_range: tuple[float, float] = (-180.0, 180.0)
    sep: str = "_"

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

    @property
    def requires(self) -> tuple[str, ...]:
        return ("stac:centroid",)

    @property
    def fields(self) -> pa.Schema:
        description = "MajorTOM spherical grid cell identifier"
        return pa.schema(
            [pa.field("code", pa.string(), nullable=False, metadata={b"description": description.encode()})]
        )

    def configuration(self) -> dict[str, Any]:
        return {
            "dist_km": self.dist_km,
            "latitude_range": list(self.latitude_range),
            "longitude_range": list(self.longitude_range),
            "sep": self.sep,
        }

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError("MajorTOM requires numpy") from exc

        points = [_point(value) for value in columns["stac:centroid"]]
        lons = np.asarray([point[0] for point in points])
        latitudes = np.asarray([point[1] for point in points])
        lats, row_labels, longitude_rows, column_labels = _grid(self.dist_km, self.latitude_range, self.longitude_range)
        row_indexes = np.searchsorted(lats, latitudes, side="left") - 1
        row_indexes = np.clip(row_indexes, 0, len(lats) - 1)
        column_indexes = np.empty_like(row_indexes)
        for row_index in np.unique(row_indexes):
            selected = row_indexes == row_index
            row_lons = longitude_rows[int(row_index)]
            if not row_lons.size:
                raise ValueError("longitude_range does not contain a MajorTOM grid column")
            indexes = np.searchsorted(row_lons, lons[selected], side="left") - 1
            indexes[indexes < 0] = row_lons.size - 1
            column_indexes[selected] = indexes

        distance = f"{int(self.dist_km):04d}km"
        codes = [
            self.sep.join(
                (
                    distance,
                    str(row_labels[int(row_index)]),
                    str(column_labels[int(row_index)][int(column_index)]),
                )
            )
            for row_index, column_index in zip(row_indexes, column_indexes, strict=True)
        ]
        return {"code": codes}


@dataclass(frozen=True, init=False)
class GeoEnrich(DerivedMetadata):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    variables: tuple[str, ...]
    scale_m: float
    _PRODUCTS: ClassVar[dict[str, tuple[str, str | None, bool, str]]] = {
        "elevation": ("projects/sat-io/open-datasets/GLO-30", None, True, "mean"),
        "cisi": ("projects/sat-io/open-datasets/CISI/global_CISI", None, False, "mean"),
        "precipitation": ("projects/ee-csaybar-real/assets/precipitation", None, False, "mean"),
        "temperature": ("projects/ee-csaybar-real/assets/temperature", None, False, "mean"),
        "soil_clay": ("OpenLandMap/SOL/SOL_CLAY-WFRACTION_USDA-3A1A1A_M/v02", "b0", False, "mean"),
        "soil_sand": ("OpenLandMap/SOL/SOL_SAND-WFRACTION_USDA-3A1A1A_M/v02", "b0", False, "mean"),
        "soil_carbon": ("OpenLandMap/SOL/SOL_ORGANIC-CARBON_USDA-6A1C_M/v02", "b0", False, "mean"),
        "soil_bulk_density": ("OpenLandMap/SOL/SOL_BULKDENS-FINEEARTH_USDA-4A1H_M/v02", "b0", False, "mean"),
        "soil_ph": ("OpenLandMap/SOL/SOL_PH-H2O_USDA-4C1A2A_M/v02", "b0", False, "mean"),
        "gdp": (
            "projects/sat-io/open-datasets/GRIDDED_HDI_GDP/total_gdp_perCapita_1990_2022_5arcmin",
            "PPP_2022",
            False,
            "mean",
        ),
        "human_modification": (
            "projects/sat-io/open-datasets/GHM/HM_1990_2020_OVERALL_300M",
            "constant",
            True,
            "mean",
        ),
        "population": ("projects/sat-io/open-datasets/hrsl/hrslpop", None, True, "mean"),
        "admin_countries": ("projects/ee-csaybar-real/assets/admin0", None, False, "mode"),
        "admin_states": ("projects/ee-csaybar-real/assets/admin1", None, False, "mode"),
        "admin_districts": ("projects/ee-csaybar-real/assets/admin2", None, False, "mode"),
    }

    def __init__(self, variables: tuple[str, ...] | list[str] | None = None, *, scale_m: float = 5120) -> None:
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
        object.__setattr__(self, "variables", selected)
        object.__setattr__(self, "scale_m", float(scale_m))

    @property
    def requires(self) -> tuple[str, ...]:
        return ("stac:centroid",)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema(
            pa.field(name, pa.string() if name.startswith("admin_") else pa.float32(), nullable=True)
            for name in self.variables
        )

    def configuration(self) -> dict[str, Any]:
        return {"variables": list(self.variables), "scale_m": self.scale_m}

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        try:
            from importlib import import_module

            ee = import_module("ee")
        except ImportError as exc:
            raise ImportError("GeoEnrich requires earthengine-api; install taco-eo[geoenrich]") from exc
        points = [(index, *_point(value)) for index, value in enumerate(columns["stac:centroid"])]
        features = [ee.Feature(ee.Geometry.Point(lon, lat), {"taco_index": index}) for index, lon, lat in points]
        collection = ee.FeatureCollection(features)
        result: dict[str, list[Any]] = {name: [None] * len(points) for name in self.variables}

        def fetch(name: str) -> tuple[str, dict[int, Any]]:
            path, band, image_collection, reducer = self._PRODUCTS[name]
            image = ee.ImageCollection(path).mosaic() if image_collection else ee.Image(path)
            if band is not None:
                image = image.select(band)
            image = image.rename(name)
            reducer_value = ee.Reducer.mean() if reducer == "mean" else ee.Reducer.mode()
            response = image.reduceRegions(
                collection=collection,
                reducer=reducer_value,
                scale=self.scale_m,
            ).getInfo()
            values = {}
            for feature in response.get("features", []):
                properties = feature.get("properties", {})
                index = properties.get("taco_index")
                if isinstance(index, int):
                    value = properties.get(name)
                    if name.startswith("admin_") and value is not None:
                        value = str(value)
                    values[index] = value
            return name, values

        with ThreadPoolExecutor(max_workers=min(8, len(self.variables))) as executor:
            for name, values in executor.map(fetch, self.variables):
                for index, value in values.items():
                    result[name][index] = value
        return result


__all__ = ["ISTAC", "STAC", "GeoEnrich", "MajorTOM", "Split"]
