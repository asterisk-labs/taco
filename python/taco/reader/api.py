from __future__ import annotations

from collections.abc import Sequence
from os import PathLike, fspath
from pathlib import Path
from typing import overload

import pyarrow as pa

from . import native
from .dataset import Dataset
from .source import Source


def open_dataset(source: Source) -> Dataset:
    """Open a local or remote TACO dataset."""
    return Dataset(source)


def read(
    source: Source | Dataset,
    *,
    files: str | Sequence[str] | None = None,
) -> pa.Table:
    """Read all samples as a wide Arrow table."""
    dataset = source if isinstance(source, Dataset) else open_dataset(source)
    return dataset.read(files=files)


@overload
def read_bytes(location: str | PathLike[str]) -> bytes: ...  # type: ignore[overload-overlap]


@overload
def read_bytes(location: Sequence[str | PathLike[str]]) -> list[bytes]: ...


def read_bytes(location: str | PathLike[str] | Sequence[str | PathLike[str]]) -> bytes | list[bytes]:
    """Read bytes at one location or return a list for a sequence.

    A location is a ``::location`` value, or any local path or URI the reader
    opens. Sequence results keep their input order.
    """
    if isinstance(location, (str, PathLike)):
        return native.read_bytes([_expand(location)])[0]
    if not location:
        return []
    return native.read_bytes([_expand(value) for value in location])


def _expand(location: str | PathLike[str]) -> str:
    value = fspath(location)
    return str(Path(value).expanduser()) if value.startswith("~") else value


__all__ = ["Dataset", "open_dataset", "read", "read_bytes"]
