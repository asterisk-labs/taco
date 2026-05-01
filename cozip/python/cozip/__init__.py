"""cozip — Cloud-Optimized ZIP."""

from importlib.metadata import PackageNotFoundError, version

from ._core import CozipError, ffi, lib
from ._writer import create
from ._reader import read

try:
    __version__ = version("cozip")
except PackageNotFoundError:
    # Running from a checkout without `pip install`.
    __version__ = "0.0.0+unknown"

# Version of the underlying libcozip binary. This may differ from
# __version__ when a Python-only release re-uses a libcozip binary
# from a previous tag (no need to recompile C if nothing in core/
# changed). Exposed mostly for debugging — `__version__` is the
# canonical version users care about.
__version_c__ = ffi.string(lib.cozip_version_string()).decode("ascii")

__all__ = ["CozipError", "create", "ffi", "lib", "__version__", "__version_c__"]