from __future__ import annotations

import json
from collections.abc import Sequence
from os import fspath
from typing import Any

import pyarrow as pa

from .._source import Location, PathInput, Source, labels, normalize
from ..errors import ContainerError
from .engine import EXTENSION_ENV, connect, reset

__all__ = [
    "EXTENSION_ENV",
    "collection",
    "connect",
    "contract",
    "derived",
    "levels",
    "profile",
    "read",
    "reset",
    "sql",
    "structure",
]


Index = int | Sequence[int] | None


def _text(path: PathInput | Location) -> str:
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
    path: Source,
    *,
    idx: Index = None,
    level: str | None = None,
    pivoted: bool = True,
    files: Sequence[str] | None = None,
    gdal_vsi: bool = True,
) -> pa.Table:
    """Read a dataset through ``read_taco()``.

    ``path`` is a ``.zip`` archive, a FOLDER directory, a ``.tacocat``
    catalog or a sequence of compatible partitions. Multiple paths are
    combined by DuckDB and include ``source_file`` in the result. ``idx``
    selects local sample positions in every source. ``level`` returns one
    contract level raw, with its internal columns. ``pivoted`` gives one row
    per sample with a column per file; ``False`` gives one row per file.
    ``files`` restricts which structure leaves become columns. ``gdal_vsi``
    fills the path columns.
    """
    paths = normalize(path)
    options = [_idx(idx), level, pivoted, None if files is None else list(files), gdal_vsi]
    call = "read_taco(?, idx := ?, level := ?, pivoted := ?, files := ?, gdal_vsi := ?)"
    if len(paths) == 1:
        query = f"SELECT * FROM {call}"
        arguments: list[object] = [_text(paths[0]), *options]
    else:
        projection = (
            "?::VARCHAR AS source_file, taco.*"
            if level is not None
            else ("taco.sample_id, ?::VARCHAR AS source_file, taco.* EXCLUDE (sample_id)")
        )
        branch = f"SELECT {projection} FROM {call} AS taco"
        query = " UNION ALL BY NAME ".join(branch for _ in paths)
        arguments = []
        for label, source in zip(labels(paths), paths, strict=True):
            arguments.extend([label, _text(source), *options])
    return connect().execute(query, arguments).to_arrow_table()


def contract(path: PathInput) -> pa.Table:
    """The contract as rows of ``kind`` and ``value``."""
    return connect().execute("SELECT * FROM taco_contract(?)", [_text(path)]).to_arrow_table()


def _string_list(query: str, path: PathInput) -> list[str]:
    value = _scalar(query, [_text(path)])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContainerError(f"the cozip extension returned an invalid string list for {path!r}")
    return value


def structure(path: PathInput) -> list[str]:
    """``taco:structure``, empty when the contract declares none."""
    return _string_list("SELECT taco_structure(?)", path)


def levels(path: PathInput) -> list[str]:
    """The metadata levels, parents before children."""
    return _string_list("SELECT taco_levels(?)", path)


def derived(path: PathInput) -> dict[str, Any]:
    """The serialized ``taco:derived`` declarations."""
    values = _string_list("SELECT taco_derived(?)", path)
    if not values:
        return {}
    if len(values) != 1:
        raise ContainerError(f"the cozip extension returned invalid taco:derived for {path!r}")
    try:
        data = json.loads(values[0])
    except json.JSONDecodeError as exc:
        raise ContainerError(f"the cozip extension returned invalid taco:derived for {path!r}") from exc
    if not isinstance(data, dict):
        raise ContainerError(f"the cozip extension returned invalid taco:derived for {path!r}")
    return data


def collection(path: PathInput) -> dict[str, Any]:
    """``COLLECTION.json``, parsed."""
    value = _scalar("SELECT taco_collection(?)", [_text(path)])
    return _collection_dict(value, path)


def _collections(paths: Sequence[Location]) -> list[dict[str, Any]]:
    rows = (
        connect()
        .execute(
            "SELECT taco_collection(path) FROM unnest(?::VARCHAR[]) WITH ORDINALITY AS sources(path, position) "
            "ORDER BY position",
            [[_text(path) for path in paths]],
        )
        .fetchall()
    )
    if len(rows) != len(paths):
        raise ContainerError("the cozip extension did not return every COLLECTION.json")
    return [_collection_dict(row[0], path) for row, path in zip(rows, paths, strict=True)]


def _collection_dict(value: object, path: PathInput | Location) -> dict[str, Any]:
    if not isinstance(value, (str, bytes, bytearray)):
        raise ContainerError(f"the cozip extension returned invalid COLLECTION.json for {path!r}")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ContainerError(f"the cozip extension returned a non-object COLLECTION.json for {path!r}")
    return data


def _string_scalar(query: str, path: PathInput) -> str:
    value = _scalar(query, [_text(path)])
    if not isinstance(value, str):
        raise ContainerError(f"the cozip extension returned an invalid string for {path!r}")
    return value


def profile(path: PathInput) -> str:
    """The cozip profile of an archive: ``none``, ``flat`` or ``taco``."""
    return _string_scalar("SELECT cozip_profile(?)", path)


def sql(
    path: PathInput,
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
