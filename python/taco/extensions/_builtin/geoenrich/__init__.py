"""GeoEnrich extension."""

from .extension import GeoEnrich

MODELS: dict[str, type] = {}

__all__ = ["MODELS", "GeoEnrich"]
