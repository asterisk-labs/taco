from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar, cast

import pyarrow as pa

from ....contract.extension import Extension, ExtensionContext
from ....contract.naming import validate_field_name
from ....errors import SampleError
from ..stac.models import centroid_field, lonlat


def _distance_label(value: float) -> str:
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


def _row_label(context: ExtensionContext, row: int) -> str:
    ids = context.columns.get("id")
    return repr(ids[row]) if ids is not None and ids[row] is not None else f"row {row}"


def _label(index: int, positive: str, negative: str) -> str:
    return f"{index:04d}{positive}" if index >= 0 else f"{-index:04d}{negative}"


@lru_cache(maxsize=32)
def _grid(dist_km: float) -> tuple[Any, int, tuple[Any, ...], tuple[int, ...]]:
    """Return the start of every row, the equator row, and the column starts and prime meridian column of each row."""
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("MajorTOM requires numpy") from exc

    divisions = math.ceil(math.pi * 6378.137 / dist_km)
    lats = np.linspace(-90, 90, divisions + 1)[:-1]
    lats = np.sort(np.mod(lats, 180) - 90)
    equator = int(np.searchsorted(lats, 0, side="left"))
    longitude_rows = []
    meridians = []
    for lat in lats:
        circumference = 2 * math.pi * 6378.137 * math.cos(math.radians(float(lat)))
        count = max(1, math.ceil(circumference / dist_km))
        lons = np.sort(np.mod(np.linspace(-180, 180, count + 1)[:-1], 360) - 180)
        zero_hits = np.flatnonzero(lons == 0)
        meridians.append(int(zero_hits[0]) if zero_hits.size else int(np.argmin(np.abs(lons))))
        longitude_rows.append(lons)
    return lats, equator, tuple(longitude_rows), tuple(meridians)


@dataclass(frozen=True)
class MajorTOM(Extension):
    """Assign the spherical MajorTOM grid cell containing each centroid."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    # A code names the cell of a sample, and validation reads it there.
    __taco_levels__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_nullable__: ClassVar[bool] = False
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

    def _codes(self, longitudes: Any, latitudes: Any, dist_km: float) -> list[str | None]:
        """Return the code of each centroid, or None south of the first row."""
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError("MajorTOM requires numpy") from exc

        lats, equator, longitude_rows, meridians = _grid(dist_km)
        # A centroid on an edge belongs to the cell south or west of it.
        row_indexes = np.searchsorted(lats, latitudes, side="left") - 1
        columns: Any = np.zeros_like(row_indexes)
        for row_index in np.unique(row_indexes[row_indexes >= 0]):
            selected = row_indexes == row_index
            row_lons = longitude_rows[int(row_index)]
            indexes: Any = np.searchsorted(row_lons, longitudes[selected], side="left") - 1
            # West of the first column is the last one, which reaches the antimeridian.
            indexes[indexes < 0] = row_lons.size - 1
            columns[selected] = indexes - meridians[int(row_index)]

        distance = f"MT{_distance_label(dist_km)}km"
        return [
            None if row < 0 else self.sep.join((distance, _label(row - equator, "U", "D"), _label(column, "R", "L")))
            for row, column in zip(row_indexes.tolist(), columns.tolist(), strict=True)
        ]

    def _grid_codes(self, centroids: Sequence[Any], label: Callable[[int], str]) -> dict[str, list[str]]:
        """Return the codes of every grid; ``label`` names a row whose centroid the grid does not cover."""
        try:
            import numpy as np
        except ImportError as exc:
            raise ImportError("MajorTOM requires numpy") from exc

        points = [lonlat(value, field=self.centroid) for value in centroids]
        longitudes = np.asarray([point[0] for point in points], dtype=np.float64)
        latitudes = np.asarray([point[1] for point in points], dtype=np.float64)
        (south, north), (west, east) = self.latitude_range, self.longitude_range
        outside = np.flatnonzero((latitudes < south) | (latitudes > north) | (longitudes < west) | (longitudes > east))
        if outside.size:
            row = int(outside[0])
            raise SampleError(
                f"{self.centroid} {points[row]} of {label(row)} is outside the MajorTOM latitude_range "
                f"{list(self.latitude_range)} or longitude_range {list(self.longitude_range)}"
            )
        result = {}
        for name, distance in self._grids():
            codes = self._codes(longitudes, latitudes, distance)
            if None in codes:
                row = codes.index(None)
                raise SampleError(
                    f"{self.centroid} {points[row]} of {label(row)} is south of the first MajorTOM row at "
                    f"{_distance_label(distance)} km, which starts at latitude {float(_grid(distance)[0][0]):.4f}"
                )
            result[name] = codes
        return cast(dict[str, list[str]], result)

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return self._grid_codes(context.columns[self.centroid], lambda row: _row_label(context, row))

    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        return self._grid_codes(columns[self.centroid], lambda row: f"row {row}")


__all__ = ["MajorTOM"]
