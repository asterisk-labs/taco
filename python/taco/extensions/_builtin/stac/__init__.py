"""STAC extension."""

from .checks import check_dataset
from .models import PROFILES, STAC, Point, Spatial, Temporal, grid_bbox, grid_bboxes, grid_footprint

MODELS = dict(PROFILES)

__all__ = [
    "MODELS",
    "STAC",
    "Point",
    "Spatial",
    "Temporal",
    "check_dataset",
    "grid_bbox",
    "grid_bboxes",
    "grid_footprint",
]
