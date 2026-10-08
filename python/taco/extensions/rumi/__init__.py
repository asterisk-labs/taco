"""The Rumi extension: canonical headers and statistics of Rumi files."""

from ...contract.extension_registry import BUILTINS
from .extension import Rumi

IDENTIFIER, NAMESPACES = BUILTINS["rumi"]

__all__ = ["IDENTIFIER", "NAMESPACES", "Rumi"]
