from __future__ import annotations

import math
import struct
import tempfile
from array import array
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar, TypeAlias

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


class _SpatialExtent(CollectionSummary):
    """Incrementally summarize EPSG:4326 sample centroids."""

    field = "extent"
    requires: ClassVar[tuple[str, ...]] = ("centroid",)

    def __init__(self) -> None:
        self._longitudes = tempfile.TemporaryFile()  # noqa: SIM115
        self._longitude_count = 0
        self._south = 90.0
        self._north = -90.0
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

    def _spatial(self) -> tuple[float, float, float, float] | None:
        if not self._longitude_count:
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
        del longitudes
        return west, self._south, east, self._north

    def finish(self) -> dict[str, Any] | None:
        if self._finished:
            return self._result
        self._finished = True
        spatial = self._spatial()
        if spatial is not None:
            self._result = Extent(spatial).to_dict()
        self.close()
        return self._result

    def close(self) -> None:
        if not self._longitudes.closed:
            self._longitudes.close()


class _SpatioTemporalExtent(_SpatialExtent):
    """Incrementally summarize sample centroids and time ranges."""

    requires: ClassVar[tuple[str, ...]] = ("centroid", "time_start", "time_end")

    def __init__(self) -> None:
        super().__init__()
        self._start: datetime | None = None
        self._end: datetime | None = None

    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        super().update(columns)
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
        spatial = self._spatial()
        if spatial is not None:
            temporal = (
                None if self._start is None or self._end is None else (_iso_utc(self._start), _iso_utc(self._end))
            )
            self._result = Extent(spatial, temporal).to_dict()
        self.close()
        return self._result


def _positive_shape(value: ShapeND) -> ShapeND:
    if any(size <= 0 for size in value):
        raise ValueError("tensor_shape dimensions must be positive")
    return value


def _finite_geotransform(value: GeoTransform6) -> GeoTransform6:
    if not all(math.isfinite(item) for item in value):
        raise ValueError("geotransform values must be finite")
    return value


def _validate_centroid(value: bytes | None, namespace: str) -> bytes | None:
    if value is not None:
        point_from_wkb(value, field=f"{namespace}:centroid")
    return value


def _validate_time_range(model: Any) -> None:
    if model.time_end is not None and model.time_start > model.time_end:
        raise ValueError("time_start must not be after time_end")


class Spatial(SampleModel):
    """Spatial metadata for regular raster chunks."""

    __taco_namespace__ = "spatial"
    __taco_summaries__ = (_SpatialExtent,)

    crs: str = Field(min_length=1, description="Coordinate reference system (WKT2, EPSG, or PROJ)")
    tensor_shape: ShapeND = Field(min_length=2, description="Tensor dimensions, ending in height and width")
    geotransform: Annotated[
        GeoTransform6,
        pa.list_(pa.field("item", pa.float64(), nullable=False)),
    ] = Field(description="Six-value GDAL affine geotransform")
    centroid: bytes | None = Field(default=None, description="Optional centroid override in EPSG:4326 as WKB")

    _shape = field_validator("tensor_shape")(_positive_shape)
    _transform = field_validator("geotransform")(_finite_geotransform)

    @field_validator("centroid")
    @classmethod
    def _centroid(cls, value: bytes | None) -> bytes | None:
        return _validate_centroid(value, "spatial")


class ISpatial(SampleModel):
    """Spatial metadata for samples with irregular footprints."""

    __taco_namespace__ = "ispatial"
    __taco_summaries__ = (_SpatialExtent,)

    crs: str = Field(min_length=1, description="Coordinate reference system (WKT2, EPSG, or PROJ)")
    geometry: bytes = Field(min_length=5, description="Spatial footprint in the declared CRS as WKB")
    centroid: bytes | None = Field(default=None, description="Optional centroid override in EPSG:4326 as WKB")

    @field_validator("centroid")
    @classmethod
    def _centroid(cls, value: bytes | None) -> bytes | None:
        return _validate_centroid(value, "ispatial")


class Temporal(SampleModel):
    """Temporal metadata for samples without a required spatial profile."""

    __taco_namespace__ = "temporal"

    time_start: TimestampUTC = Field(description="Acquisition start")
    time_end: TimestampUTC | None = Field(default=None, description="Acquisition end")
    time_middle: TimestampUTC | None = Field(default=None, description="Acquisition midpoint")

    @model_validator(mode="after")
    def _times(self) -> Temporal:
        _validate_time_range(self)
        return self


class STAC(SampleModel):
    """Combined regular spatial and temporal metadata."""

    __taco_namespace__ = "stac"
    __taco_summaries__ = (_SpatioTemporalExtent,)

    crs: str = Field(min_length=1, description="Coordinate reference system (WKT2, EPSG, or PROJ)")
    tensor_shape: ShapeND = Field(min_length=2, description="Tensor dimensions, ending in height and width")
    geotransform: Annotated[
        GeoTransform6,
        pa.list_(pa.field("item", pa.float64(), nullable=False)),
    ] = Field(description="Six-value GDAL affine geotransform")
    time_start: TimestampUTC = Field(description="Acquisition start")
    centroid: bytes | None = Field(default=None, description="Optional centroid override in EPSG:4326 as WKB")
    time_end: TimestampUTC | None = Field(default=None, description="Acquisition end")
    time_middle: TimestampUTC | None = Field(default=None, description="Acquisition midpoint")

    _shape = field_validator("tensor_shape")(_positive_shape)
    _transform = field_validator("geotransform")(_finite_geotransform)

    @field_validator("centroid")
    @classmethod
    def _centroid(cls, value: bytes | None) -> bytes | None:
        return _validate_centroid(value, "stac")

    @model_validator(mode="after")
    def _times(self) -> STAC:
        _validate_time_range(self)
        return self


class ISTAC(SampleModel):
    """Combined irregular spatial and temporal metadata."""

    __taco_namespace__ = "istac"
    __taco_summaries__ = (_SpatioTemporalExtent,)

    crs: str = Field(min_length=1, description="Coordinate reference system (WKT2, EPSG, or PROJ)")
    geometry: bytes = Field(min_length=5, description="Spatial footprint in the declared CRS as WKB")
    time_start: TimestampUTC = Field(description="Acquisition start")
    time_end: TimestampUTC | None = Field(default=None, description="Acquisition end")
    time_middle: TimestampUTC | None = Field(default=None, description="Acquisition midpoint")
    centroid: bytes | None = Field(default=None, description="Optional centroid override in EPSG:4326 as WKB")

    @field_validator("centroid")
    @classmethod
    def _centroid(cls, value: bytes | None) -> bytes | None:
        return _validate_centroid(value, "istac")

    @model_validator(mode="after")
    def _times(self) -> ISTAC:
        _validate_time_range(self)
        return self


__all__ = ["ISTAC", "STAC", "ISpatial", "Spatial", "Temporal"]
