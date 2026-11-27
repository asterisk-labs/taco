from __future__ import annotations

from collections.abc import Sequence
from os import PathLike, fspath
from pathlib import Path
from typing import TypeAlias

Location: TypeAlias = str | Path
PathInput: TypeAlias = str | PathLike[str]
Source: TypeAlias = PathInput | Sequence[PathInput]


def normalize_source(source: PathInput) -> Location:
    """Keep a value containing ``://`` as written; expand and resolve a local path."""
    value = fspath(source)
    if not isinstance(value, str):
        raise TypeError("source paths must resolve to strings")
    if not value:
        raise ValueError("source paths must not be empty")
    return value if "://" in value else Path(value).expanduser().resolve()


def _identity(source: Location) -> object:
    # Local sources are the same when they are one file, as a path and its
    # file:// URI are, or two spellings on a case-insensitive file system. The
    # core opens a file:// URI as the path that follows the prefix.
    if isinstance(source, str):
        if not source.startswith("file://"):
            return source
        source = Path(source.removeprefix("file://"))
    try:
        status = source.stat()
    except (OSError, ValueError):
        return source
    return (status.st_dev, status.st_ino)


def normalize_sources(source: Source) -> tuple[Location, ...]:
    items: tuple[PathInput, ...]
    if isinstance(source, (str, PathLike)):
        items = (source,)
    elif isinstance(source, Sequence):
        items = tuple(source)
    else:
        raise TypeError("source must be a path or a sequence of paths")
    if not items:
        raise ValueError("source must contain at least one path")

    paths = tuple(normalize_source(item) for item in items)
    if len(paths) != len({_identity(path) for path in paths}):
        raise ValueError("source paths must be unique")
    return paths


__all__ = ["Location", "PathInput", "Source", "normalize_source", "normalize_sources"]
