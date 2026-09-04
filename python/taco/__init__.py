from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .contract import Asset, Collection, Contract, Sample
from .errors import TacoError
from .tacocat import consolidate
from .validate import validate
from .writer import open_folder, open_writer

try:
    # The import name is `taco`; the distribution is `taco-eo` on PyPI.
    __version__ = version("taco-eo")
except PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0+unknown"

__all__ = [
    "Asset",
    "Collection",
    "Contract",
    "Sample",
    "TacoError",
    "__version__",
    "consolidate",
    "open_folder",
    "open_writer",
    "validate",
]
