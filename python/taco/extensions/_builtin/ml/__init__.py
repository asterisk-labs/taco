"""ML extension."""

from .checks import check_dataset
from .models import ML

MODELS: dict[str, type] = {}

__all__ = ["ML", "MODELS", "check_dataset"]
