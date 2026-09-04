from __future__ import annotations

import json
import os
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


def _text(path: str | os.PathLike[str]) -> str:
    return os.fspath(path)


def _scalar(query: str, arguments: list[Any]) -> Any:
    """Run a one-row, one-column query. The extension always returns a row."""
    row = connect().execute(query, arguments).fetchone()
    if row is None:
        raise ContainerError(f"the cozip extension returned no row for {arguments[0]!r}")
    return row[0]


def _idx(value: int | list[int] | tuple[int, int] | None) -> str | None:
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
    path: str | os.PathLike[str],
    *,
    idx: int | list[int] | tuple[int, int] | None = None,
    level: str | None = None,
    pivoted: bool = True,
    files: list[str] | None = None,
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
            [_text(path), _idx(idx), level, pivoted, files, gdal_vsi],
        )
        .to_arrow_table()
    )


def contract(path: str | os.PathLike[str]) -> pa.Table:
    """The contract as rows of ``kind`` and ``value``."""
    return connect().execute("SELECT * FROM taco_contract(?)", [_text(path)]).to_arrow_table()


def structure(path: str | os.PathLike[str]) -> list[str]:
    """``taco:structure``, empty when the contract declares none."""
    return _scalar("SELECT taco_structure(?)", [_text(path)])


def levels(path: str | os.PathLike[str]) -> list[str]:
    """The metadata levels, parents before children."""
    return _scalar("SELECT taco_levels(?)", [_text(path)])


def collection(path: str | os.PathLike[str]) -> dict[str, Any]:
    """``COLLECTION.json``, parsed."""
    return json.loads(_scalar("SELECT taco_collection(?)", [_text(path)]))


def profile(path: str | os.PathLike[str]) -> str:
    """The cozip profile of an archive: ``none``, ``flat`` or ``taco``."""
    return _scalar("SELECT cozip_profile(?)", [_text(path)])


def sql(
    path: str | os.PathLike[str],
    *,
    idx: int | list[int] | tuple[int, int] | None = None,
    level: str | None = None,
    pivoted: bool = True,
    files: list[str] | None = None,
    gdal_vsi: bool = True,
) -> str:
    """The query ``read_taco()`` would run, for debugging."""
    return str(_scalar("SELECT taco_sql(?, ?, ?, ?, ?, ?)", [_text(path), _idx(idx), level, pivoted, files, gdal_vsi]))
