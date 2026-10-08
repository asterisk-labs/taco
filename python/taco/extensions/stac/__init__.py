"""The STAC extension: temporal, spatial and STAC metadata profiles."""

from ...contract.extension_registry import BUILTINS
from . import folder
from .models import (
    STAC,
    Point,
    Spatial,
    Temporal,
    centroid_field,
    check_location,
    check_times,
    footprint_bbox,
    footprint_center,
    grid_bbox,
    grid_bboxes,
    grid_center,
    grid_centers,
    grid_footprint,
    grid_problems,
    load_footprint,
    longitude_cover,
    lonlat,
    to_float32,
)

IDENTIFIER, NAMESPACES = BUILTINS["stac"]

__all__ = [
    "IDENTIFIER",
    "NAMESPACES",
    "STAC",
    "Point",
    "Spatial",
    "Temporal",
    "centroid_field",
    "check_location",
    "check_times",
    "folder",
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
