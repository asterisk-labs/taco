"""Split extension."""

from .checks import check_dataset
from .models import Split

MODELS = {"split": Split}

__all__ = ["MODELS", "Split", "check_dataset"]
