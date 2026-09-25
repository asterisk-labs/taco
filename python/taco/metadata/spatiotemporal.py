from __future__ import annotations

import math
import re
import struct
import tempfile
import threading
from array import array
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar, TypeAlias

import pyarrow as pa
from pydantic import Field, field_validator, model_validator

from ..contract.collection import Extent
from ._base import CollectionSummary, SampleModel

TimestampUTC = Annotated[datetime, pa.timestamp("us", tz="UTC")]
Geometry: TypeAlias = bytes
BBox: TypeAlias = Annotated[
    tuple[float, float, float, float],
    pa.list_(pa.field("item", pa.float64(), nullable=False)),
]
GridShape: TypeAlias = Annotated[tuple[int, int], pa.list_(pa.field("item", pa.int64(), nullable=False))]
GridTransform: TypeAlias = Annotated[
    tuple[float, float, float, float, float, float],
    pa.list_(pa.field("item", pa.float64(), nullable=False)),
]

_PROJ_CODE = re.compile(r"[A-Z][A-Z0-9_]*:[A-Za-z0-9_.\-]+")
_PROJ_FIELDS = ("proj_code", "proj_shape", "proj_transform")
_WGS84_CODES = frozenset({"EPSG:4326", "OGC:CRS84"})
# Retain sampled edges: straight edges in the source CRS can curve in WGS84.
_EDGE_STEPS = 16
_CHUNK = 1_000_000
_transformers = threading.local()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


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


def point_wkb(longitude: float, latitude: float) -> bytes:
    """Encode an EPSG:4326 point as little-endian WKB."""
    return struct.pack("<BIdd", 1, 1, longitude, latitude)


def longitude_cover(starts: Any, ends: Any) -> tuple[float, float]:
    """Return the narrowest ``(west, east)`` covering longitude intervals sorted by start.

    The result leaves out the largest gap between the intervals, so it crosses
    the antimeridian, with west greater than east, when that gap does not.
    """
    import numpy as np

    count = len(starts)
    first = float(starts[0])
    reach = float(ends[0])
    best_gap, west, east = -math.inf, first, reach
    for offset in range(1, count, _CHUNK):
        block_starts = np.asarray(starts[offset : offset + _CHUNK], dtype=np.float64)
        block_ends = np.asarray(ends[offset : offset + _CHUNK], dtype=np.float64)
        reaches = np.maximum.accumulate(np.concatenate(([reach], block_ends)))
        gaps = block_starts - reaches[:-1]
        index = int(np.argmax(gaps))
        if gaps[index] > best_gap:
            best_gap = float(gaps[index])
            west, east = float(block_starts[index]), float(reaches[index])
        reach = float(reaches[-1])
    # On a tie the gap across the antimeridian wins, so the result does not cross it.
    wrap = first + 360 - reach
    if wrap >= best_gap:
        best_gap, west, east = wrap, first, reach
    if best_gap <= 0:
        return -180.0, 180.0
    return west, east


def _parts(shape: Any) -> list[Any]:
    import shapely

    return list(shapely.get_parts(shape))


def _crosses_unsplit(shape: Any) -> bool:
    import numpy as np
    import shapely

    lines = []
    for part in _parts(shape):
        if part.geom_type == "Polygon":
            lines.extend(shapely.get_rings(part))
        elif part.geom_type == "LineString":
            lines.append(part)
    for line in lines:
        coordinates = shapely.get_coordinates(line)
        longitudes = coordinates[:, 0]
        # An edge between -180 and 180 runs along the antimeridian or spans every
        # longitude, as in a polar cap; any other edge longer than 180 degrees
        # is a footprint that should have been split.
        long_edges = np.abs(np.diff(longitudes)) > 180
        on_antimeridian = (np.abs(longitudes[:-1]) == 180) & (np.abs(longitudes[1:]) == 180)
        on_pole = (np.abs(coordinates[:-1, 1]) == 90) & (coordinates[:-1, 1] == coordinates[1:, 1])
        if (long_edges & ~on_antimeridian & ~on_pole).any():
            return True
    return False


def load_footprint(value: bytes, *, field: str = "geometry") -> Any:
    """Decode and check an EPSG:4326 WKB footprint."""
    import numpy as np
    import shapely

    try:
        shape = shapely.from_wkb(value)
    except Exception as exc:
        raise ValueError(f"{field} is not valid WKB") from exc
    if shape is None or shape.is_empty:
        raise ValueError(f"{field} is empty")
    if shape.geom_type == "GeometryCollection":
        raise ValueError(f"{field} must not be a GeometryCollection")
    if shapely.has_z(shape):
        raise ValueError(f"{field} must be two-dimensional")
    coordinates = shapely.get_coordinates(shape)
    if not np.isfinite(coordinates).all():
        raise ValueError(f"{field} contains non-finite coordinates")
    if not shapely.is_valid(shape):
        raise ValueError(f"{field} is not a valid geometry: {shapely.is_valid_reason(shape)}")
    longitudes, latitudes = coordinates[:, 0], coordinates[:, 1]
    if longitudes.min() < -180 or longitudes.max() > 180 or latitudes.min() < -90 or latitudes.max() > 90:
        raise ValueError(f"{field} is outside EPSG:4326 bounds")
    if _crosses_unsplit(shape):
        raise ValueError(f"{field} crosses the antimeridian; split it at 180 degrees as RFC 7946 requires")
    return shape


def footprint_bbox(shape: Any) -> tuple[float, float, float, float]:
    """Return ``(west, south, east, north)``; west is greater than east across the antimeridian."""
    import numpy as np
    import shapely

    bounds = shapely.bounds(shapely.get_parts(shape))
    bounds = bounds[np.isfinite(bounds).all(axis=1)]
    bounds = bounds[np.argsort(bounds[:, 0], kind="stable")]
    west, east = longitude_cover(bounds[:, 0], bounds[:, 2])
    return west, float(bounds[:, 1].min()), east, float(bounds[:, 3].max())


def footprint_center(value: bytes, *, field: str = "geometry") -> tuple[float, float]:
    """Return the ``(longitude, latitude)`` centroid of an EPSG:4326 WKB footprint."""
    import numpy as np
    import shapely

    shape = load_footprint(value, field=field)
    west, _, east, _ = footprint_bbox(shape)
    if west > east:
        # Move the parts east of the antimeridian past 180 so the footprint is contiguous.
        shift = np.array([360.0, 0.0])
        shape = shapely.transform(shape, lambda xy: np.where(xy[:, :1] < west, xy + shift, xy))
    center = shape.centroid
    longitude = float(center.x)
    # Adding zero turns a centroid at -0.0 into 0.0.
    return (longitude - 360 if longitude > 180 else longitude) + 0.0, float(center.y) + 0.0


def _to_wgs84(code: str) -> Any:
    cache = getattr(_transformers, "cache", None)
    if cache is None:
        cache = _transformers.cache = {}
    if code not in cache:
        from pyproj import CRS, Transformer
        from pyproj.exceptions import CRSError

        try:
            source = CRS.from_user_input(code)
        except CRSError as exc:
            raise ValueError(f"proj_code {code!r} is not a known CRS") from exc
        cache[code] = Transformer.from_crs(source, CRS.from_epsg(4326), always_xy=True)
    return cache[code]


def grid_footprint(code: str, shape: Sequence[int], transform: Sequence[float]) -> bytes:
    """Return the EPSG:4326 WKB footprint of a regular grid.

    Edges are approximated by 16 segments per side after reprojection. Grids
    crossing the antimeridian are split there; polar grids retain their curved
    boundary and close through the enclosed pole.
    """
    import numpy as np
    import shapely
    from shapely.affinity import translate
    from shapely.geometry import MultiPolygon, Polygon, box
    from shapely.geometry.polygon import orient

    rows, columns = (int(value) for value in shape)
    a, b, c, d, e, f = (float(value) for value in transform)
    steps = np.linspace(0.0, 1.0, _EDGE_STEPS + 1)[:-1]
    zeros, ones = np.zeros_like(steps), np.ones_like(steps)
    # Clockwise from the top-left corner along the pixel edges.
    column = np.concatenate((steps, ones, 1 - steps, zeros)) * columns
    row = np.concatenate((zeros, steps, ones, 1 - steps)) * rows
    x = a * column + b * row + c
    y = d * column + e * row + f
    if code in _WGS84_CODES:
        longitudes, latitudes = x, y
    else:
        longitudes, latitudes = (np.asarray(value, dtype=np.float64) for value in _to_wgs84(code).transform(x, y))
    if not (np.isfinite(longitudes).all() and np.isfinite(latitudes).all()):
        raise ValueError(f"the grid in {code} cannot be projected to EPSG:4326")
    if latitudes.min() < -90 or latitudes.max() > 90:
        raise ValueError(f"the grid in {code} extends beyond the poles")

    unwrapped = np.unwrap(longitudes, period=360)
    closing = (longitudes[0] - longitudes[-1] + 180) % 360 - 180
    winding = unwrapped[-1] - unwrapped[0] + closing
    ring = list(zip(unwrapped.tolist(), latitudes.tolist(), strict=True))
    if abs(winding) > 180:
        pole = 90.0 if latitudes.mean() >= 0 else -90.0
        end = float(unwrapped[0] + winding)
        ring.extend([(end, float(latitudes[0])), (end, pole), (float(unwrapped[0]), pole)])
    polygon = Polygon(ring)
    west, _, east, _ = polygon.bounds
    if east - west > 360 + 1e-8:
        raise ValueError(f"the grid in {code} wraps more than once around the earth")
    if west >= -180 and east <= 180:
        footprint = orient(polygon)
    else:
        pieces: list[Any] = []
        for band in range(math.floor((west + 180) / 360), math.floor((east + 180) / 360) + 1):
            clipped = polygon.intersection(box(-180 + 360 * band, -90, 180 + 360 * band, 90))
            pieces.extend(
                translate(piece, xoff=-360 * band)
                for piece in _parts(clipped)
                if piece.geom_type == "Polygon" and not piece.is_empty
            )
        # Polar pieces share a seam away from the antimeridian. Merge it to
        # avoid producing an invalid MultiPolygon with a shared edge.
        footprint = shapely.union_all(pieces)
        if footprint.geom_type == "Polygon":
            footprint = orient(footprint)
        else:
            footprint = MultiPolygon([orient(piece) for piece in _parts(footprint)])
    result = bytes(shapely.to_wkb(footprint, byte_order=1, output_dimension=2))
    load_footprint(result)
    return result


def grid_center(code: str, shape: Sequence[int], transform: Sequence[float]) -> bytes:
    """Return the center of a regular grid as an EPSG:4326 WKB point.

    The center is taken in the grid CRS and reprojected alone, so it does not
    depend on how the footprint approximates the grid edges.
    """
    rows, columns = (int(value) for value in shape)
    a, b, c, d, e, f = (float(value) for value in transform)
    x = a * columns / 2 + b * rows / 2 + c
    y = d * columns / 2 + e * rows / 2 + f
    if code in _WGS84_CODES:
        longitude, latitude = x, y
    else:
        longitude, latitude = (float(value) for value in _to_wgs84(code).transform(x, y))
    if not (math.isfinite(longitude) and math.isfinite(latitude)) or not -90 <= latitude <= 90:
        raise ValueError(f"the grid center in {code} cannot be projected to EPSG:4326")
    longitude = (longitude + 180) % 360 - 180 if not -180 <= longitude <= 180 else longitude
    return point_wkb(longitude + 0.0, latitude + 0.0)


def check_location(values: Mapping[str, Any]) -> None:
    """Apply the Spatial and STAC rules to one group of values."""
    given = [values[name] is not None for name in _PROJ_FIELDS]
    if any(given) and not all(given):
        raise ValueError("proj_code, proj_shape and proj_transform must be given together")
    if values["geometry"] is None and not all(given):
        raise ValueError("geometry is required unless proj_code, proj_shape and proj_transform are given")
    if values["bbox"] is not None:
        if values["geometry"] is None:
            raise ValueError("bbox requires geometry")
        expected = footprint_bbox(load_footprint(values["geometry"]))
        if tuple(values["bbox"]) != expected:
            raise ValueError(f"bbox does not match geometry; expected {list(expected)}")


def check_times(values: Mapping[str, Any]) -> None:
    """Apply the STAC datetime rules to one group of values."""
    start, end = values["start_datetime"], values["end_datetime"]
    if (start is None) != (end is None):
        raise ValueError("start_datetime and end_datetime must be given together")
    if values["datetime"] is None and start is None:
        raise ValueError("datetime is required unless start_datetime and end_datetime are given")
    if start is not None and _as_utc(start) > _as_utc(end):
        raise ValueError("start_datetime must not be after end_datetime")


class _Location(SampleModel):
    """Fields and checks shared by Spatial and STAC."""

    geometry: Geometry | None = Field(
        default=None,
        description="Footprint in EPSG:4326 as WKB",
    )
    bbox: BBox | None = Field(
        default=None,
        description="Footprint bounds [west, south, east, north]; west > east crosses the antimeridian",
    )
    centroid: bytes | None = Field(
        default=None,
        description="Center of the grid, or of the footprint without a grid, as an EPSG:4326 WKB point",
    )

    @field_validator("centroid")
    @classmethod
    def _centroid(cls, value: bytes | None) -> bytes | None:
        if value is not None:
            point_from_wkb(value)
        return value

    @field_validator("geometry")
    @classmethod
    def _geometry(cls, value: bytes | None) -> bytes | None:
        if value is not None:
            load_footprint(value)
        return value

    @field_validator("proj_code", check_fields=False)
    @classmethod
    def _proj_code(cls, value: str | None) -> str | None:
        if value is not None and not _PROJ_CODE.fullmatch(value):
            raise ValueError("proj_code must look like AUTHORITY:CODE, such as EPSG:32718")
        return value

    @field_validator("proj_shape", check_fields=False)
    @classmethod
    def _proj_shape(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and min(value) <= 0:
            raise ValueError("proj_shape must contain a positive height and width")
        return value

    @field_validator("proj_transform", check_fields=False)
    @classmethod
    def _proj_transform(cls, value: tuple[float, ...] | None) -> tuple[float, ...] | None:
        if value is None:
            return value
        if not all(math.isfinite(item) for item in value):
            raise ValueError("proj_transform values must be finite")
        a, b, _, d, e, _ = value
        if a * e - b * d == 0:
            raise ValueError("proj_transform must not collapse the grid")
        return value


class Temporal(SampleModel):
    """When the sample was observed, as a STAC datetime or datetime range."""

    __taco_namespace__ = "temporal"

    datetime: TimestampUTC | None = Field(
        default=None,
        description="Acquisition time; null when only a range is known",
    )
    start_datetime: TimestampUTC | None = Field(
        default=None,
        description="First time covered by the observation, inclusive",
    )
    end_datetime: TimestampUTC | None = Field(
        default=None,
        description="Last time covered by the observation, inclusive",
    )

    @model_validator(mode="after")
    def _times(self) -> Temporal:
        check_times(vars(self))
        return self


class _SpatialExtent(CollectionSummary):
    """Incrementally summarize sample bounding boxes."""

    field = "extent"
    requires: ClassVar[tuple[str, ...]] = ("bbox",)

    def __init__(self) -> None:
        self._intervals = tempfile.TemporaryFile()  # noqa: SIM115
        self._interval_count = 0
        self._south = 90.0
        self._north = -90.0
        self._result: dict[str, Any] | None = None
        self._finished = False

    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        intervals = array("d")
        for bbox in columns["bbox"]:
            if bbox is None:
                continue
            west, south, east, north = (float(value) for value in bbox)
            if west <= east:
                intervals.extend((west, east))
            else:
                intervals.extend((west, 180.0, -180.0, east))
            self._south = min(self._south, south)
            self._north = max(self._north, north)
        intervals.tofile(self._intervals)
        self._interval_count += len(intervals) // 2

    def _spatial(self) -> tuple[float, float, float, float] | None:
        if not self._interval_count:
            return None

        import numpy as np

        self._intervals.flush()
        intervals: Any = np.memmap(
            self._intervals,
            dtype=[("start", np.float64), ("end", np.float64)],
            mode="r+",
            shape=(self._interval_count,),
        )
        intervals.sort(order="start")
        west, east = longitude_cover(intervals["start"], intervals["end"])
        del intervals
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
        if not self._intervals.closed:
            self._intervals.close()


class _SpatioTemporalExtent(_SpatialExtent):
    """Incrementally summarize sample bounding boxes and times."""

    requires: ClassVar[tuple[str, ...]] = ("bbox", "datetime", "start_datetime", "end_datetime")

    def __init__(self) -> None:
        super().__init__()
        self._start: datetime | None = None
        self._end: datetime | None = None

    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        super().update(columns)
        for moment, first, last in zip(
            columns["datetime"], columns["start_datetime"], columns["end_datetime"], strict=True
        ):
            starts = [_as_utc(value) for value in (moment, first) if value is not None]
            ends = [_as_utc(value) for value in (moment, last) if value is not None]
            if not starts:
                continue
            start, end = min(starts), max(ends)
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


class Spatial(_Location):
    """Where the sample is, as an EPSG:4326 footprint and an optional grid."""

    __taco_namespace__ = "spatial"
    __taco_summaries__ = (_SpatialExtent,)

    proj_code: str | None = Field(default=None, description="CRS of the grid as AUTHORITY:CODE, e.g. EPSG:32718")
    proj_shape: GridShape | None = Field(default=None, description="Grid size in pixels as [height, width]")
    proj_transform: GridTransform | None = Field(
        default=None,
        description="Affine transform [a, b, c, d, e, f]: x = a*column + b*row + c, y = d*column + e*row + f",
    )

    @model_validator(mode="after")
    def _location(self) -> Spatial:
        check_location(vars(self))
        return self


class STAC(_Location):
    """Where and when the sample was observed, with the fields and rules of a STAC Item."""

    __taco_namespace__ = "stac"
    __taco_summaries__ = (_SpatioTemporalExtent,)

    datetime: TimestampUTC | None = Field(
        default=None,
        description="Acquisition time; null when only a range is known",
    )
    start_datetime: TimestampUTC | None = Field(
        default=None,
        description="First time covered by the observation, inclusive",
    )
    end_datetime: TimestampUTC | None = Field(
        default=None,
        description="Last time covered by the observation, inclusive",
    )
    proj_code: str | None = Field(default=None, description="CRS of the grid as AUTHORITY:CODE, e.g. EPSG:32718")
    proj_shape: GridShape | None = Field(default=None, description="Grid size in pixels as [height, width]")
    proj_transform: GridTransform | None = Field(
        default=None,
        description="Affine transform [a, b, c, d, e, f]: x = a*column + b*row + c, y = d*column + e*row + f",
    )

    @model_validator(mode="after")
    def _location_and_times(self) -> STAC:
        check_location(vars(self))
        check_times(vars(self))
        return self


__all__ = [
    "STAC",
    "Spatial",
    "Temporal",
    "check_location",
    "check_times",
    "footprint_bbox",
    "footprint_center",
    "grid_center",
    "grid_footprint",
    "load_footprint",
    "longitude_cover",
    "point_from_wkb",
    "point_wkb",
]
