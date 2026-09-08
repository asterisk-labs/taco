from __future__ import annotations

import math
import struct
import tempfile
from array import array
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Annotated, Any, TypeAlias

import pyarrow as pa
from pydantic import Field, field_validator, model_validator

from ..contract.collection import Extent
from ._base import CollectionSummary, SampleModel

TimestampUTC = Annotated[datetime, pa.timestamp("us", tz="UTC")]
ShapeND: TypeAlias = tuple[int, ...]
GeoTransform6: TypeAlias = tuple[float, float, float, float, float, float]


def point_from_wkb(wkb: bytes, *, field: str = "centroid") -> tuple[float, float]:
    """Decode and validate an EPSG:4326 WKB point."""
    if len(wkb) < 21 or wkb[0] not in (0, 1):
        raise ValueError(f"{field} must be a WKB point")
    order = "<" if wkb[0] == 1 else ">"
    geometry_type = struct.unpack_from(order + "I", wkb, 1)[0]
    offset = 5
    if geometry_type & 0x20000000:
        offset += 4
    if geometry_type & 0xFF != 1:
        raise ValueError(f"{field} must be a WKB point")
    if len(wkb) < offset + 16:
        raise ValueError(f"{field} contains incomplete WKB")
    longitude, latitude = struct.unpack_from(order + "dd", wkb, offset)
    valid = math.isfinite(longitude) and math.isfinite(latitude) and -180 <= longitude <= 180 and -90 <= latitude <= 90
    if not valid:
        raise ValueError(f"{field} is outside EPSG:4326 bounds")
    return longitude, latitude


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


class _SpatioTemporalExtent(CollectionSummary):
    """Incrementally summarize sample centroids and time ranges."""

    field = "extent"
    requires = ("centroid", "time_start", "time_end")

    def __init__(self) -> None:
        # Longitudes are sorted only at the end to find the smallest bounding
        # interval across the antimeridian.  A temporary file keeps RAM flat.
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
            longitude, latitude = point_from_wkb(value)
            longitudes.append(-180 if longitude == 180 else longitude)
            self._south = min(self._south, latitude)
            self._north = max(self._north, latitude)
        longitudes.tofile(self._longitudes)
        self._longitude_count += len(longitudes)

        for start, end in zip(columns["time_start"], columns["time_end"], strict=True):
            if start is None:
                continue
            start = _as_utc(start)
            end = start if end is None else _as_utc(end)
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
        longitudes: Any = np.memmap(self._longitudes, dtype=np.float64, mode="r+", shape=(self._longitude_count,))
        longitudes.sort()
        largest_gap = float(longitudes[0] + 360 - longitudes[-1])
        gap_index = self._longitude_count - 1
        for start in range(0, self._longitude_count - 1, 1_000_000):
            values = longitudes[start : min(self._longitude_count, start + 1_000_001)]
            differences = np.diff(values)
            index = int(np.argmax(differences))
            if differences[index] > largest_gap:
                largest_gap = float(differences[index])
                gap_index = start + index
        west = float(longitudes[(gap_index + 1) % self._longitude_count])
        east = float(longitudes[gap_index])
        temporal = None if self._start is None or self._end is None else (_iso_utc(self._start), _iso_utc(self._end))
        self._result = Extent((west, self._south, east, self._north), temporal).to_dict()
        del longitudes
        self.close()
        return self._result

    def close(self) -> None:
        self._longitudes.close()


def _validate_time_range(model: STAC | ISTAC) -> None:
    if model.time_end is not None and model.time_start > model.time_end:
        raise ValueError("time_start must not be after time_end")
    if model.time_middle is None and model.time_end is not None:
        midpoint = model.time_start + (model.time_end - model.time_start) / 2
        object.__setattr__(model, "time_middle", midpoint)


class STAC(SampleModel):
    """Compact spatiotemporal metadata for regular raster chunks.

    The affine grid reconstructs the footprint, so STAC does not duplicate a
    WKB geometry per chunk.  Only ``centroid`` is WKB, always in EPSG:4326.
    """

    __taco_namespace__ = "stac"
    __taco_summaries__ = (_SpatioTemporalExtent,)

    crs: str = Field(min_length=1, description="Coordinate reference system (WKT2, EPSG, or PROJ)")
    tensor_shape: ShapeND = Field(min_length=2, description="Tensor dimensions, ending in height and width")
    geotransform: Annotated[GeoTransform6, pa.list_(pa.float64())] = Field(
        description="Six-value GDAL affine geotransform"
    )
    time_start: TimestampUTC = Field(description="Acquisition start")
    centroid: bytes = Field(description="Centroid in EPSG:4326 as WKB")
    time_end: TimestampUTC | None = Field(default=None, description="Acquisition end")
    time_middle: TimestampUTC | None = Field(default=None, description="Acquisition midpoint")

    @field_validator("tensor_shape")
    @classmethod
    def _positive_shape(cls, value: ShapeND) -> ShapeND:
        if any(size <= 0 for size in value):
            raise ValueError("tensor_shape dimensions must be positive")
        return value

    @field_validator("geotransform")
    @classmethod
    def _finite_geotransform(cls, value: GeoTransform6) -> GeoTransform6:
        if not all(math.isfinite(item) for item in value):
            raise ValueError("geotransform values must be finite")
        return value

    @field_validator("centroid")
    @classmethod
    def _valid_centroid(cls, value: bytes) -> bytes:
        point_from_wkb(value, field="stac:centroid")
        return value

    @model_validator(mode="after")
    def _times(self) -> STAC:
        _validate_time_range(self)
        return self


class ISTAC(SampleModel):
    """Spatiotemporal metadata for samples with irregular footprints.

    ``geometry`` is WKB in ``crs``.  ``centroid`` remains a WKB point in
    EPSG:4326 so collection summaries and spatial indexes share one fast path.
    """

    __taco_namespace__ = "istac"
    __taco_summaries__ = (_SpatioTemporalExtent,)

    crs: str = Field(min_length=1, description="Coordinate reference system (WKT2, EPSG, or PROJ)")
    geometry: bytes = Field(min_length=5, description="Spatial footprint in the declared CRS as WKB")
    time_start: TimestampUTC = Field(description="Acquisition start")
    time_end: TimestampUTC | None = Field(default=None, description="Acquisition end")
    time_middle: TimestampUTC | None = Field(default=None, description="Acquisition midpoint")
    centroid: bytes = Field(description="Centroid in EPSG:4326 as WKB")

    @field_validator("centroid")
    @classmethod
    def _valid_centroid(cls, value: bytes) -> bytes:
        point_from_wkb(value, field="istac:centroid")
        return value

    @model_validator(mode="after")
    def _times(self) -> ISTAC:
        _validate_time_range(self)
        return self


__all__ = ["ISTAC", "STAC"]
