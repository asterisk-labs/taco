from __future__ import annotations

import importlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from .container.parquet import Encoding
from .contract import Asset, Collection, Contract, Folder, Sample
from .contract.extension import ExtensionContext
from .contract.schema import DerivedMetadata, Extension, Level, Metadata
from .errors import TacoError
from .reader import Dataset, open_dataset, read
from .reader.inspect import inspect

if TYPE_CHECKING:  # pragma: no cover - typing only
    from . import extensions as extensions
    from . import metadata as metadata
    from .validate import validate as validate
    from .writer import open_writer as open_writer
    from .writer.catalog import consolidate as consolidate
    from .writer.export import export as export

try:
    __version__ = version("taco-eo")
except PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0+unknown"

# Writer modules load on first use.
_WRITER = {
    "consolidate": ".writer.catalog",
    "export": ".writer.export",
    "extensions": ".extensions",
    "metadata": ".metadata",
    "open_writer": ".writer",
    "validate": ".validate",
    "writer": ".writer",
}
_MODULES = frozenset({"extensions", "metadata", "writer"})
_WRITER_DEPENDENCIES = frozenset({"cozip", "numpy", "pydantic", "pydantic_core", "pyproj", "shapely"})


def __getattr__(name: str) -> Any:
    if name not in _WRITER:
        raise AttributeError(f"module 'taco' has no attribute {name!r}")
    try:
        module = importlib.import_module(_WRITER[name], __name__)
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] not in _WRITER_DEPENDENCIES:
            raise
        raise ImportError(f"taco.{name} needs the writer: pip install 'taco-eo[writer]' (missing {exc.name})") from exc
    value = module if name in _MODULES else getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _WRITER.keys())


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
    "inspect",
    "open_dataset",
    "read",
]

_WRITER_PUBLIC = ["consolidate", "export", "extensions", "metadata", "open_writer", "validate"]

try:
    if all(importlib.util.find_spec(name) is not None for name in _WRITER_DEPENDENCIES):
        __all__.extend(_WRITER_PUBLIC)
except (ImportError, ModuleNotFoundError, ValueError):
    pass
