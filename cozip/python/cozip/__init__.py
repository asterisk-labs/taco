"""cozip — Cloud-Optimized ZIP."""

from importlib.metadata import PackageNotFoundError, version

from ._core import CozipError, ffi, lib
from ._writer import create
from ._reader import read

try:
    __version__ = version("cozip")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["CozipError", "create", "read", "ffi", "lib", "__version__"]