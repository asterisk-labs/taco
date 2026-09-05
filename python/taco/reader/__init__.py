from __future__ import annotations

import json
from collections.abc import Sequence
from os import PathLike, fspath
from typing import Any

import pyarrow as pa

from ..errors import ContainerError
from .engine import EXTENSION_ENV, connect, reset

__all__ = [
    "EXTENSION_ENV",
    "collection",
    "connect",
    "contract",
    "levels",
    "profile",
    "read",
    "reset",
    "sql",
    "structure",
]


Index = int | Sequence[int] | None


def _text(path: str | PathLike[str]) -> str:
    return fspath(path)


def _scalar(query: str, arguments: list[object]) -> object:
    """Run a one-row, one-column query. The extension always returns a row."""
    row = connect().execute(query, arguments).fetchone()
    if row is None:
        raise ContainerError(f"the cozip extension returned no row for {arguments[0]!r}")
    return row[0]


def _idx(value: Index) -> str | None:
    """``read_taco`` takes idx as an integer or a two-element range."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("idx must be an integer or a two-element range")
    if isinstance(value, int):
        return str(value)
    values = list(value)
    if len(values) != 2 or not all(isinstance(item, int) and not isinstance(item, bool) for item in values):
        raise TypeError("idx must be an integer or a two-element range")
    return f"[{values[0]}, {values[1]}]"


def read(
    path: str | PathLike[str],
    *,
    idx: Index = None,
    level: str | None = None,
    pivoted: bool = True,
    files: Sequence[str] | None = None,
    gdal_vsi: bool = True,
) -> pa.Table:
    """Read a dataset through ``read_taco()``.

    ``path`` is a ``.zip`` archive, a FOLDER directory or a ``.tacocat``
    catalog, local or remote. ``idx`` selects one sample or a half-open
    range. ``level`` returns one contract level raw, with its internal
    columns. ``pivoted`` gives one row per sample with a column per file;
    ``False`` gives one row per file. ``files`` restricts which structure
    leaves become columns. ``gdal_vsi`` fills the path columns.
    """
    return (
        connect()
        .execute(
            "SELECT * FROM read_taco(?, idx := ?, level := ?, pivoted := ?, files := ?, gdal_vsi := ?)",
            [_text(path), _idx(idx), level, pivoted, None if files is None else list(files), gdal_vsi],
        )
        .to_arrow_table()
    )


def contract(path: str | PathLike[str]) -> pa.Table:
    """The contract as rows of ``kind`` and ``value``."""
    return connect().execute("SELECT * FROM taco_contract(?)", [_text(path)]).to_arrow_table()


def _string_list(query: str, path: str | PathLike[str]) -> list[str]:
    value = _scalar(query, [_text(path)])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContainerError(f"the cozip extension returned an invalid string list for {path!r}")
    return value


def structure(path: str | PathLike[str]) -> list[str]:
    """``taco:structure``, empty when the contract declares none."""
    return _string_list("SELECT taco_structure(?)", path)


def levels(path: str | PathLike[str]) -> list[str]:
    """The metadata levels, parents before children."""
    return _string_list("SELECT taco_levels(?)", path)


def collection(path: str | PathLike[str]) -> dict[str, Any]:
    """``COLLECTION.json``, parsed."""
    value = _scalar("SELECT taco_collection(?)", [_text(path)])
    if not isinstance(value, (str, bytes, bytearray)):
        raise ContainerError(f"the cozip extension returned invalid COLLECTION.json for {path!r}")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ContainerError(f"the cozip extension returned a non-object COLLECTION.json for {path!r}")
    return data


def _string_scalar(query: str, path: str | PathLike[str]) -> str:
    value = _scalar(query, [_text(path)])
    if not isinstance(value, str):
        raise ContainerError(f"the cozip extension returned an invalid string for {path!r}")
    return value


def profile(path: str | PathLike[str]) -> str:
    """The cozip profile of an archive: ``none``, ``flat`` or ``taco``."""
    return _string_scalar("SELECT cozip_profile(?)", path)


def sql(
    path: str | PathLike[str],
    *,
    idx: Index = None,
    level: str | None = None,
    pivoted: bool = True,
    files: Sequence[str] | None = None,
    gdal_vsi: bool = True,
) -> str:
    """The query ``read_taco()`` would run, for debugging."""
    value = _scalar(
        "SELECT taco_sql(?, ?, ?, ?, ?, ?)",
        [_text(path), _idx(idx), level, pivoted, None if files is None else list(files), gdal_vsi],
    )
    if not isinstance(value, str):
        raise ContainerError(f"the cozip extension returned invalid SQL for {path!r}")
    return value
