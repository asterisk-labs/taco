"""Instrument extension."""

from .checks import check_dataset
from .models import Instrument

MODELS: dict[str, type] = {}

__all__ = ["MODELS", "Instrument", "check_dataset"]
