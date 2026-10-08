"""Rumi extension."""

from .extension import Rumi

MODELS: dict[str, type] = {}

__all__ = ["MODELS", "Rumi"]
