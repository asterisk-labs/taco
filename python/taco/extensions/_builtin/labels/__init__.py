"""Labels extension."""

from .checks import check_dataset
from .models import Labels

MODELS: dict[str, type] = {}

__all__ = ["MODELS", "Labels", "check_dataset"]
