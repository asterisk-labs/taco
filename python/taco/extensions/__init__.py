"""Built-in extensions, one module per extension in ``docs/spec/extensions``."""

from . import geoenrich, majortom, rumi, stac
from .geoenrich import GeoEnrich
from .majortom import MajorTOM
from .rumi import Rumi
from .stac.extension import STAC, Spatial

__all__ = ["STAC", "GeoEnrich", "MajorTOM", "Rumi", "Spatial", "geoenrich", "majortom", "rumi", "stac"]
