"""The MajorTOM extension: grid cells of each sample centroid."""

from ...contract.extension_registry import BUILTINS
from .extension import MajorTOM

IDENTIFIER, NAMESPACES = BUILTINS["majortom"]

__all__ = ["IDENTIFIER", "NAMESPACES", "MajorTOM"]
