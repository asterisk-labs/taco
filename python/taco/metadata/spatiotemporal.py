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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..container.parquet import Encoding
from ..contract.collection import Extent
from ._base import CollectionSummary, SampleModel

TimestampUTC = Annotated[datetime, pa.timestamp("us", tz="UTC")]
Float32 = Annotated[float, pa.float32()]
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
_EDGE_DEPTH = 12
_METRES_PER_DEGREE = math.pi * 6_371_008.8 / 180
_CHUNK = 1_000_000
_transformers = threading.local()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def to_float32(value: float) -> float:
    """Round a coordinate to the float32 value stored for it."""
    rounded: float = struct.unpack("<f", struct.pack("<f", value))[0]
    return rounded


class Point(BaseModel):
    """An EPSG:4326 float32 point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lon: Float32 = Field(description="Longitude in degrees")
    lat: Float32 = Field(description="Latitude in degrees")

    @model_validator(mode="before")
    @classmethod
    def _pair(cls, value: Any) -> Any:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return {"lon": value[0], "lat": value[1]}
        return value

    @field_validator("lon", "lat")
    @classmethod
    def _coordinate(cls, value: float, info: Any) -> float:
        limit = 180 if info.field_name == "lon" else 90
        if not math.isfinite(value) or not -limit <= value <= limit:
            raise ValueError(f"{info.field_name} is outside EPSG:4326 bounds")
        return to_float32(value)


def lonlat(value: Any, *, field: str = "centroid") -> tuple[float, float]:
    """Return the ``(longitude, latitude)`` of a stored point."""
    try:
        point = value if isinstance(value, Point) else Point.model_validate(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a point with lon and lat in EPSG:4326") from exc
    return point.lon, point.lat


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


def _degrees_per_unit(code: str) -> float:
    if code in _WGS84_CODES:
        return 1.0
    return float(_to_wgs84(code).source_crs.axis_info[0].unit_conversion_factor) * 180 / math.pi


def _metres_per_unit(code: str) -> float:
    if _is_geographic(code):
        return _degrees_per_unit(code) * _METRES_PER_DEGREE
    return float(_to_wgs84(code).source_crs.axis_info[0].unit_conversion_factor)


def _half_pixel_units(transform: Any) -> Any:
    import numpy as np

    a, b, _, d, e, _ = np.moveaxis(np.asarray(transform, dtype=np.float64), -1, 0)
    return np.sqrt(np.abs(a * e - b * d)) / 2


def _offset_metres(off_lon: Any, off_lat: Any) -> Any:
    import numpy as np

    # Longitude is not scaled by latitude, so an edge along a pole, where the
    # ring must still turn, is split like any other.
    return np.hypot(off_lon, off_lat) * _METRES_PER_DEGREE


def _transform(code: str, x: Any, y: Any, direction: str = "FORWARD") -> tuple[Any, Any]:
    import numpy as np

    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    flat_x, flat_y = x.ravel(), y.ravel()
    # pyproj before 3.8 warns on a one-element array under NumPy 2, so a single
    # point goes in twice.
    if flat_x.size == 1:
        flat_x, flat_y = np.repeat(flat_x, 2), np.repeat(flat_y, 2)
    out_x, out_y = _to_wgs84(code).transform(flat_x, flat_y, direction=direction)
    out_x = np.asarray(out_x, dtype=np.float64)[: x.size].reshape(x.shape)
    out_y = np.asarray(out_y, dtype=np.float64)[: x.size].reshape(x.shape)
    return out_x, out_y


def _project(code: str, x: Any, y: Any) -> tuple[Any, Any]:
    import numpy as np

    if code in _WGS84_CODES:
        return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    return _transform(code, x, y)


def _check_lonlat(code: str, longitudes: Any, latitudes: Any) -> None:
    import numpy as np

    if not (np.isfinite(longitudes).all() and np.isfinite(latitudes).all()):
        raise ValueError(f"the grid in {code} cannot be projected to EPSG:4326")
    if latitudes.min() < -90 or latitudes.max() > 90:
        raise ValueError(f"the grid in {code} extends beyond the poles")


def _is_geographic(code: str) -> bool:
    return code in _WGS84_CODES or bool(_to_wgs84(code).source_crs.is_geographic)


def _lonlat(code: str, x: Any, y: Any, slack: Any) -> tuple[Any, Any]:
    """Project to EPSG:4326 while preserving longitude turns."""
    import numpy as np

    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    longitudes, latitudes = _project(code, x, y)
    _check_lonlat(code, longitudes, latitudes)
    if _is_geographic(code):
        base = x * _degrees_per_unit(code)
        return base + (longitudes - base + 180) % 360 - 180, latitudes
    back_x, back_y = _transform(code, longitudes, latitudes, direction="INVERSE")
    if not ((np.abs(back_x - x) <= slack) & (np.abs(back_y - y) <= slack)).all():
        raise ValueError(f"the grid in {code} extends beyond the area its CRS represents")
    return longitudes, latitudes


def _edge_offsets(start: tuple[Any, Any], end: tuple[Any, Any], middle: tuple[Any, Any], geographic: bool) -> Any:
    step = end[0] - start[0]
    off_lon = middle[0] - start[0] - step / 2
    if not geographic:
        # A projected longitude comes back wrapped to [-180, 180].
        step = (step + 180) % 360 - 180
        off_lon = (middle[0] - start[0] - step / 2 + 180) % 360 - 180
    return _offset_metres(off_lon, middle[1] - (start[1] + end[1]) / 2)


def _check_turns(code: str, longitudes: Any, tolerance: Any = 0.0) -> None:
    if (longitudes.max(axis=-1) - longitudes.min(axis=-1) > 360 + tolerance + 1e-9).any():
        raise ValueError(f"the grid in {code} wraps more than once around the earth")


def _grid_ring(code: str, shape: Sequence[int], transform: Sequence[float]) -> tuple[Any, Any]:
    import numpy as np

    rows, columns = (int(value) for value in shape)
    a, b, c, d, e, f = (float(value) for value in transform)
    geographic = _is_geographic(code)
    slack = _half_pixel_units(transform)
    tolerance = slack * _metres_per_unit(code)

    def lonlat(pixels: Any) -> tuple[Any, Any]:
        return _lonlat(code, a * pixels[:, 0] + b * pixels[:, 1] + c, d * pixels[:, 0] + e * pixels[:, 1] + f, slack)

    pixels = np.array([[0, 0], [columns, 0], [columns, rows], [0, rows], [0, 0]], dtype=np.float64)
    if geographic:
        # Turns are counted on the grid itself: a datum shift is not exactly periodic.
        _check_turns(code, (a * pixels[:4, 0] + b * pixels[:4, 1] + c) * _degrees_per_unit(code))
    longitudes, latitudes = lonlat(pixels)
    for _ in range(_EDGE_DEPTH):
        middle = (pixels[:-1] + pixels[1:]) / 2
        mid_lon, mid_lat = lonlat(middle)
        start, end = (longitudes[:-1], latitudes[:-1]), (longitudes[1:], latitudes[1:])
        split = _edge_offsets(start, end, (mid_lon, mid_lat), geographic) > tolerance
        if not split.any():
            break
        at = np.flatnonzero(split) + 1
        pixels = np.insert(pixels, at, middle[split], axis=0)
        longitudes = np.insert(longitudes, at, mid_lon[split])
        latitudes = np.insert(latitudes, at, mid_lat[split])
    longitudes, latitudes = longitudes[:-1], latitudes[:-1]
    if not geographic:
        longitudes = np.unwrap(longitudes, period=360)
        _check_turns(code, longitudes, tolerance / _METRES_PER_DEGREE)
    return longitudes, latitudes


def _winding(longitudes: Any) -> Any:
    closing = (longitudes[..., 0] - longitudes[..., -1] + 180) % 360 - 180
    return longitudes[..., -1] - longitudes[..., 0] + closing


def _ring_bboxes(longitudes: Any, latitudes: Any) -> list[tuple[float, float, float, float]]:
    import numpy as np

    south, north = latitudes.min(axis=1), latitudes.max(axis=1)
    low, high = longitudes.min(axis=1), longitudes.max(axis=1)
    shift = 360 * np.floor((low + 180) / 360)
    west, east = low - shift, high - shift
    east = np.where(east > 180, east - 360, east)
    polar = np.abs(_winding(longitudes)) > 180
    everywhere = polar | (high - low >= 360)
    west, east = np.where(everywhere, -180.0, west), np.where(everywhere, 180.0, east)
    northern = latitudes.mean(axis=1) >= 0
    south = np.where(polar & ~northern, -90.0, south)
    north = np.where(polar & northern, 90.0, north)
    # Adding zero turns -0.0 into 0.0.
    return [
        (float(w) + 0.0, float(s) + 0.0, float(e) + 0.0, float(n) + 0.0)
        for w, s, e, n in zip(west, south, east, north, strict=True)
    ]


def grid_footprint(code: str, shape: Sequence[int], transform: Sequence[float]) -> bytes:
    """Return a grid's EPSG:4326 WKB footprint."""
    import numpy as np
    import shapely
    from shapely.affinity import translate
    from shapely.geometry import MultiPolygon, Polygon, box
    from shapely.geometry.polygon import orient

    unwrapped, latitudes = _grid_ring(code, shape, transform)
    winding = float(_winding(unwrapped))
    ring = list(zip(unwrapped.tolist(), latitudes.tolist(), strict=True))
    if abs(winding) > 180:
        pole = 90.0 if latitudes.mean() >= 0 else -90.0
        end = float(unwrapped[0] + winding)
        ring.extend([(end, float(latitudes[0])), (end, pole), (float(unwrapped[0]), pole)])
    polygon = Polygon(ring)
    west, south, east, north = polygon.bounds
    # Closing a polar ring through its pole must not add a turn either.
    half_pixel = float(_half_pixel_units(transform)) * _metres_per_unit(code) / _METRES_PER_DEGREE
    _check_turns(code, np.array([west, east]), half_pixel)
    if abs(winding) <= 180 and east - west >= 360 - half_pixel:
        # A band around the whole earth; a datum shift can leave its ends a hair apart.
        footprint = orient(box(-180, south, 180, north))
    elif west >= -180 and east <= 180:
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


def grid_bbox(code: str, shape: Sequence[int], transform: Sequence[float]) -> tuple[float, float, float, float]:
    """Return ``(west, south, east, north)`` of a grid footprint without building the polygon."""
    longitudes, latitudes = _grid_ring(code, shape, transform)
    return _ring_bboxes(longitudes[None, :], latitudes[None, :])[0]


def _by_code(codes: Sequence[str]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for index, code in enumerate(codes):
        groups.setdefault(code, []).append(index)
    return groups


def grid_bboxes(
    codes: Sequence[str], shapes: Sequence[Sequence[int]], transforms: Sequence[Sequence[float]]
) -> list[tuple[float, float, float, float]]:
    """Return bounds for many grids."""
    import numpy as np

    result: list[tuple[float, float, float, float] | None] = [None] * len(codes)
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0], [1, 0.5], [0.5, 1], [0, 0.5]])
    ahead = [1, 2, 3, 0]
    for code, indices in _by_code(codes).items():
        geographic = _is_geographic(code)
        size = np.array([shapes[index] for index in indices], dtype=np.float64)
        coefficients = np.array([transforms[index] for index in indices], dtype=np.float64)
        slack = _half_pixel_units(coefficients)
        column = unit[None, :, 0] * size[:, 1:2]
        row = unit[None, :, 1] * size[:, 0:1]
        a, b, c, d, e, f = (coefficients[:, index : index + 1] for index in range(6))
        x, y = a * column + b * row + c, d * column + e * row + f
        if geographic:
            _check_turns(code, x[:, :4] * _degrees_per_unit(code))
        longitudes, latitudes = _lonlat(code, x.ravel(), y.ravel(), np.repeat(slack, len(unit)))
        longitudes, latitudes = longitudes.reshape(column.shape), latitudes.reshape(column.shape)
        start = (longitudes[:, :4], latitudes[:, :4])
        end = (longitudes[:, ahead], latitudes[:, ahead])
        offsets = _edge_offsets(start, end, (longitudes[:, 4:], latitudes[:, 4:]), geographic)
        half_pixel = slack * _metres_per_unit(code)
        straight = (offsets <= half_pixel[:, None]).all(axis=1)
        corners = start[0][straight]
        if not geographic:
            corners = np.unwrap(corners, period=360, axis=1)
            _check_turns(code, corners, half_pixel[straight] / _METRES_PER_DEGREE)
        boxes = iter(_ring_bboxes(corners, start[1][straight]))
        for position, index in enumerate(indices):
            result[index] = next(boxes) if straight[position] else grid_bbox(code, shapes[index], transforms[index])
    return [box for box in result if box is not None]


def grid_problems(
    codes: Sequence[str], shapes: Sequence[Sequence[int]], transforms: Sequence[Sequence[float]]
) -> list[tuple[int, str]]:
    """Return the position and reason of each grid without a valid footprint."""
    try:
        grid_bboxes(codes, shapes, transforms)
        return []
    except ValueError:
        pass
    problems = []
    for index, grid in enumerate(zip(codes, shapes, transforms, strict=True)):
        try:
            grid_bbox(*grid)
        except ValueError as exc:
            problems.append((index, str(exc)))
    return problems


def grid_centers(
    codes: Sequence[str], shapes: Sequence[Sequence[int]], transforms: Sequence[Sequence[float]]
) -> list[tuple[float, float]]:
    """Return EPSG:4326 centers for many grids."""
    import numpy as np

    result: list[tuple[float, float] | None] = [None] * len(codes)
    for code, indices in _by_code(codes).items():
        size = np.array([shapes[index] for index in indices], dtype=np.float64)
        coefficients = np.array([transforms[index] for index in indices], dtype=np.float64)
        a, b, c, d, e, f = coefficients.T
        column, row = size[:, 1] / 2, size[:, 0] / 2
        try:
            longitudes, latitudes = _lonlat(
                code, a * column + b * row + c, d * column + e * row + f, _half_pixel_units(coefficients)
            )
        except ValueError as exc:
            raise ValueError(f"the grid center in {code} cannot be projected to EPSG:4326: {exc}") from exc
        for index, longitude, latitude in zip(indices, longitudes.tolist(), latitudes.tolist(), strict=True):
            if not -180 <= longitude <= 180:
                longitude = (longitude + 180) % 360 - 180
            result[index] = (longitude + 0.0, latitude + 0.0)
    return [center for center in result if center is not None]


def grid_center(code: str, shape: Sequence[int], transform: Sequence[float]) -> tuple[float, float]:
    """Return the ``(longitude, latitude)`` center of one grid, as in ``grid_centers``."""
    return grid_centers([code], [shape], [transform])[0]


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
        description="Footprint in EPSG:4326 as WKB, when the producer supplies one",
    )
    bbox: BBox | None = Field(
        default=None,
        description="Bounds of geometry [west, south, east, north]; west > east crosses the antimeridian",
    )
    centroid: Point | None = Field(
        default=None,
        description="Center of the grid, or of the footprint without a grid, in EPSG:4326",
    )

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
    field = "extent"
    requires: ClassVar[tuple[str, ...]] = ("geometry", "bbox", "proj_code", "proj_shape", "proj_transform")

    def __init__(self) -> None:
        self._intervals = tempfile.TemporaryFile()  # noqa: SIM115
        self._interval_count = 0
        self._south = 90.0
        self._north = -90.0
        self._result: dict[str, Any] | None = None
        self._finished = False

    @staticmethod
    def _boxes(columns: Mapping[str, Sequence[Any]]) -> list[Sequence[float]]:
        boxes: list[Sequence[float]] = []
        grids: list[tuple[str, Sequence[int], Sequence[float]]] = []
        for geometry, bbox, code, shape, transform in zip(
            columns["geometry"],
            columns["bbox"],
            columns["proj_code"],
            columns["proj_shape"],
            columns["proj_transform"],
            strict=True,
        ):
            if bbox is not None:
                boxes.append(bbox)
            elif geometry is not None:
                boxes.append(footprint_bbox(load_footprint(geometry)))
            elif code is not None and shape is not None and transform is not None:
                grids.append((code, shape, transform))
        if grids:
            codes, shapes, transforms = zip(*grids, strict=True)
            boxes.extend(grid_bboxes(codes, shapes, transforms))
        return boxes

    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        intervals = array("d")
        for bbox in self._boxes(columns):
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

    requires: ClassVar[tuple[str, ...]] = (*_SpatialExtent.requires, "datetime", "start_datetime", "end_datetime")

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
    """Where the sample is, as a grid, an EPSG:4326 footprint, or both."""

    __taco_namespace__ = "spatial"
    __taco_summaries__ = (_SpatialExtent,)

    proj_code: Annotated[str, Encoding("dictionary")] | None = Field(
        default=None, description="CRS of the grid as AUTHORITY:CODE, e.g. EPSG:32718"
    )
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
    proj_code: Annotated[str, Encoding("dictionary")] | None = Field(
        default=None, description="CRS of the grid as AUTHORITY:CODE, e.g. EPSG:32718"
    )
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
    "Point",
    "Spatial",
    "Temporal",
    "check_location",
    "check_times",
    "footprint_bbox",
    "footprint_center",
    "grid_bbox",
    "grid_bboxes",
    "grid_center",
    "grid_centers",
    "grid_footprint",
    "grid_problems",
    "load_footprint",
    "longitude_cover",
    "lonlat",
    "to_float32",
]
