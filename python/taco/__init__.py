from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from . import extensions, metadata
from .container.parquet import Encoding
from .contract import Asset, Collection, Contract, Folder, Sample
from .contract.schema import DerivedMetadata, Extension, Level, Metadata
from .errors import TacoError
from .metadata._base import ExtensionContext
from .reader import Dataset, open_dataset, read
from .reader.inspect import inspect
from .validate import validate
from .writer import open_writer
from .writer.catalog import consolidate
from .writer.export import export

try:
    __version__ = version("taco-eo")
except PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0+unknown"

__all__ = [
    "Asset",
    "Collection",
    "Contract",
    "Dataset",
    "DerivedMetadata",
    "Encoding",
    "Extension",
    "ExtensionContext",
    "Folder",
    "Level",
    "Metadata",
    "Sample",
    "TacoError",
    "__version__",
    "consolidate",
    "export",
    "extensions",
    "inspect",
    "metadata",
    "open_dataset",
    "open_writer",
    "read",
    "validate",
]
