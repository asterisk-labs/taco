"""cozip — Cloud-Optimized ZIP."""

from importlib.metadata import PackageNotFoundError, version

from ._core import CozipError, ffi, lib
from ._writer import create

try:
    __version__ = version("cozip")
except PackageNotFoundError:
    # Running from a checkout without `pip install`.
    __version__ = "0.0.0+unknown"

__all__ = ["CozipError", "create", "ffi", "lib", "__version__"]