"""The GeoEnrich extension: environmental and administrative variables of each sample."""

from ...contract.extension_registry import BUILTINS
from .extension import GeoEnrich

IDENTIFIER, NAMESPACES = BUILTINS["geoenrich"]

__all__ = ["IDENTIFIER", "NAMESPACES", "GeoEnrich"]
