"""MajorTOM extension."""

from .checks import check_dataset
from .extension import MajorTOM

MODELS: dict[str, type] = {}

__all__ = ["MODELS", "MajorTOM", "check_dataset"]
