from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from . import metadata, reader
from .contract import Asset, Collection, Contract, Folder, Sample
from .errors import TacoError
from .schema import CollectionMetadata, DerivedMetadata, Level, Metadata, MetadataSchema
from .tacocat import consolidate
from .validate import validate
from .writer import open_writer

try:
    __version__ = version("taco-eo")
except PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0+unknown"

__all__ = [
    "Asset",
    "Collection",
    "CollectionMetadata",
    "Contract",
    "DerivedMetadata",
    "Folder",
    "Level",
    "Metadata",
    "MetadataSchema",
    "Sample",
    "TacoError",
    "__version__",
    "consolidate",
    "metadata",
    "open_writer",
    "reader",
    "validate",
]
