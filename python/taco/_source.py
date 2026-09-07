from __future__ import annotations

from collections.abc import Sequence
from os import PathLike, fspath
from pathlib import Path
from typing import TypeAlias
from urllib.parse import urlsplit

Location: TypeAlias = str | Path
PathInput: TypeAlias = str | PathLike[str]
Source: TypeAlias = PathInput | Sequence[PathInput]


def normalize(source: Source) -> tuple[Location, ...]:
    items: tuple[PathInput, ...]
    if isinstance(source, (str, PathLike)):
        items = (source,)
    elif isinstance(source, Sequence):
        items = tuple(source)
    else:
        raise TypeError("source must be a path or a sequence of paths")
    if not items:
        raise ValueError("source must contain at least one path")

    paths = []
    for item in items:
        value = fspath(item)
        if not isinstance(value, str):
            raise TypeError("source paths must resolve to strings")
        if not value:
            raise ValueError("source paths must not be empty")
        paths.append(value if "://" in value else Path(value).expanduser().resolve())

    if len(paths) != len(set(paths)):
        raise ValueError("source paths must be unique")
    return tuple(paths)


def labels(paths: tuple[Location, ...]) -> tuple[str, ...]:
    names = tuple(_name(path) for path in paths)
    if len(names) == len(set(names)) and all(names):
        return names
    return tuple(str(path) for path in paths)


def _name(path: Location) -> str:
    if isinstance(path, Path):
        return path.name
    return urlsplit(path).path.rstrip("/").rsplit("/", 1)[-1]


__all__ = ["Location", "PathInput", "Source", "labels", "normalize"]
