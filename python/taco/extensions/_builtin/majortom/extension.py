from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar

import pyarrow as pa

from ....contract.extension import Extension, ExtensionContext
from ....contract.naming import validate_field_name
from ..stac.models import centroid_field, lonlat


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
class MajorTOM(Extension):
    """Assign the spherical MajorTOM grid cell containing each centroid."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_namespace__: ClassVar[str | None] = "majortom"
    __taco_row_local__: ClassVar[bool] = True

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
        object.__setattr__(self, "centroid", centroid_field(self.centroid))
        object.__setattr__(self, "extra", self._normalized_extra())

    def _normalized_extra(self) -> tuple[tuple[str, float], ...]:
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

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return self.compute({name: context.columns[name] for name in self.requires})

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError("MajorTOM requires numpy") from exc

        points = [lonlat(value, field=self.centroid) for value in columns[self.centroid]]
        longitudes = np.asarray([point[0] for point in points])
        latitudes = np.asarray([point[1] for point in points])
        return {name: self._codes(longitudes, latitudes, distance) for name, distance in self._grids()}


__all__ = ["MajorTOM"]
