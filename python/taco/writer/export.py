from __future__ import annotations

import json
from collections.abc import Iterator
from os import PathLike, fspath
from pathlib import Path
from typing import Any

import pyarrow as pa

from ..contract.collection import Collection
from ..contract.contract import SAMPLE_ID, SAMPLE_LEVEL, Contract
from ..contract.naming import (
    CURRENT_ID,
    DATA_DIR,
    OFFSET,
    RELATIVE_PATH,
    SAMPLE_INDEX,
    SIZE,
    SOURCE_FILE,
    level_folder,
    normalize_relative_path,
)
from ..contract.sample import _PreparedAsset, _PreparedNode, _PreparedSample
from ..errors import ContainerError
from ..reader import engine, native
from ..reader.dataset import Dataset
from ..reader.source import Location, Source, normalize_sources
from .api import open_writer
from .base import BuildResult
from .progress import Progress

_BATCH_FILES = 256
_BATCH_BYTES = 256 * 1024 * 1024


def export(
    source: Source | Dataset,
    output: str | PathLike[str],
    *,
    sql: str,
    overwrite: bool = False,
    **fields: Any,
) -> BuildResult:
    """Write the complete samples selected by a SQL query.

    The query may use any dataset relation and must retain
    ``taco:sample_index``. Other keyword arguments replace collection fields.
    """
    sources = source.sources if isinstance(source, Dataset) else normalize_sources(source)
    if len(sources) != 1:
        raise ValueError("export reads one dataset")
    if "contract" in fields or "sources" in fields:
        raise ValueError("export keeps the contract and drops taco:sources")
    opened = source._opened[0] if isinstance(source, Dataset) else None
    dataset = _Source(sources[0], opened)
    selected = dataset.select(sql)
    sample_rows = dataset.level(SAMPLE_LEVEL, selected)
    collection = dataset.collection.replace(sources=None, **fields)

    with open_writer(collection, output, overwrite=overwrite, progress=True) as writer:
        total = sample_rows.num_rows
        with Progress(True, total, f"exporting {dataset.name}") as bar:
            for sample in _samples(dataset, selected, writer._stage / "export"):
                writer._add_prepared(sample)
                bar.update()
        return writer.run()


class _Source:
    def __init__(self, location: Location, opened: native.NativeDataset | None = None) -> None:
        self.location = location
        self.opened = native.NativeDataset(location) if opened is None else opened
        self.container = self.opened.container
        self.collection = Collection.from_dict(json.loads(self.opened.collection))
        self.levels: dict[str, pa.Table] = {}
        self._sample_origins: dict[tuple[str | None, str], int] | None = None
        self.pending: list[tuple[str, int, int, Path]] = []

    @property
    def name(self) -> str:
        return self.location.name if isinstance(self.location, Path) else self.location.rstrip("/").rsplit("/", 1)[-1]

    @property
    def contract(self) -> Contract:
        return self.collection.contract

    def level(self, name: str, selected: set[int] | None) -> pa.Table:
        if name not in self.levels:
            sql = native.sql([self.opened], idx=None, level=name, pivoted=True, files=None, location=False)
            connection = engine.open_reader()
            if selected is None:
                self.levels[name] = connection.execute(sql).to_arrow_table()
            else:
                # DuckDB filters while it scans the cached Parquet, before rows
                # become Python objects, and preserves the source order.
                keys = pa.table({"sample": pa.array(sorted(selected), pa.uint64())})
                connection.register("export_keys", keys)
                try:
                    if name == SAMPLE_LEVEL:
                        join = "export_keys AS keys"
                        condition = f'rows."{CURRENT_ID}" = keys.sample'
                    elif self.container == "tacocat":
                        origins = self.sample_origins()
                        chosen = [(origin, index) for origin, index in origins.items() if index in selected]
                        connection.register(
                            "export_origins",
                            pa.table(
                                {
                                    "partition": pa.array([origin[0] for origin, _ in chosen], pa.string()),
                                    "path": pa.array([origin[1] for origin, _ in chosen], pa.string()),
                                }
                            ),
                        )
                        join = "export_origins AS origin"
                        condition = (
                            f'rows."{SOURCE_FILE}" = origin.partition AND '
                            f"split_part(rows.\"{RELATIVE_PATH}\", '/', 1) = origin.path"
                        )
                    else:
                        join = "export_keys AS keys"
                        condition = f"CAST(split_part(rows.\"{RELATIVE_PATH}\", '/', 1) AS UBIGINT) = keys.sample"
                    self.levels[name] = connection.execute(
                        f"SELECT rows.* EXCLUDE (ordinal) "
                        f"FROM (SELECT *, row_number() OVER () AS ordinal FROM ({sql})) AS rows "
                        f"SEMI JOIN {join} ON {condition} ORDER BY ordinal"
                    ).to_arrow_table()
                finally:
                    connection.unregister("export_keys")
                    if name != SAMPLE_LEVEL and self.container == "tacocat":
                        connection.unregister("export_origins")
        return self.levels[name]

    def select(self, query: str) -> set[int]:
        query = _query(query)
        connection = engine.open_reader()
        relations = [
            ("dataset", native.sql([self.opened], idx=None, level=None, pivoted=True, files=None, location=True))
        ]
        relations.extend((level.replace("/", "__"), self._selection_level(level)) for level in self.contract.levels)
        context = ",\n".join(f"{_sql_identifier(name)} AS ({statement})" for name, statement in relations)
        table = connection.execute(f"WITH {context}\nSELECT * FROM (\n{query}\n) AS taco_export").to_arrow_table()
        if SAMPLE_INDEX not in table.column_names:
            raise ValueError(f"sql must return {SAMPLE_INDEX}; use SELECT * or include it explicitly")
        ids = table.column(SAMPLE_INDEX).to_pylist()
        if any(isinstance(sample, bool) or not isinstance(sample, int) or sample < 0 for sample in ids):
            raise ValueError(f"sql must return non-negative integer {SAMPLE_INDEX} values")
        return set(ids)

    def _selection_level(self, level: str) -> str:
        statement = native.sql([self.opened], idx=None, level=level, pivoted=True, files=None, location=False)
        rows = f"({statement}) AS rows"
        if level == SAMPLE_LEVEL:
            return f"SELECT rows.*, rows.{_sql_identifier(CURRENT_ID)} AS {_sql_identifier(SAMPLE_INDEX)} FROM {rows}"
        if self.container != "tacocat":
            path = f"rows.{_sql_identifier(RELATIVE_PATH)}"
            return (
                f"SELECT rows.*, CAST(split_part({path}, '/', 1) AS UBIGINT) AS {_sql_identifier(SAMPLE_INDEX)} "
                f"FROM {rows}"
            )
        return (
            f"SELECT rows.*, samples.{_sql_identifier(SAMPLE_INDEX)} FROM {rows} "
            f"JOIN {_sql_identifier(SAMPLE_LEVEL)} AS samples ON "
            f"rows.{_sql_identifier(SOURCE_FILE)} = samples.{_sql_identifier(SOURCE_FILE)} AND "
            f"split_part(rows.{_sql_identifier(RELATIVE_PATH)}, '/', 1) = "
            f"samples.{_sql_identifier(RELATIVE_PATH)}"
        )

    def sample_origins(self) -> dict[tuple[str | None, str], int]:
        if self._sample_origins is None:
            sql = native.sql([self.opened], idx=None, level=SAMPLE_LEVEL, pivoted=True, files=None, location=False)
            table = engine.open_reader().execute(sql).to_arrow_table()
            origins: dict[tuple[str | None, str], int] = {}
            for row in table.to_pylist():
                partition = row.get(SOURCE_FILE)
                if partition is not None:
                    partition = _metadata_path(row, SOURCE_FILE)
                path = _metadata_path(row, RELATIVE_PATH)
                index = row.get(CURRENT_ID)
                if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                    raise ContainerError(f"invalid dataset metadata: {CURRENT_ID} must be a non-negative integer")
                origins[(partition, path)] = index
            self._sample_origins = origins
        return self._sample_origins

    def rows(self, level: str, table: pa.Table) -> Iterator[tuple[int, dict[str, Any]]]:
        origins = self.sample_origins() if level != SAMPLE_LEVEL and self.container == "tacocat" else None
        for row in table.to_pylist():
            if level == SAMPLE_LEVEL:
                sample = row.get(CURRENT_ID)
            else:
                relative_path = _metadata_path(row, RELATIVE_PATH)
                prefix = relative_path.split("/", 1)[0]
                source_file = row.get(SOURCE_FILE)
                if source_file is not None:
                    source_file = _metadata_path(row, SOURCE_FILE)
                if origins is not None:
                    sample = origins.get((source_file, prefix))
                else:
                    try:
                        sample = int(prefix)
                    except ValueError:
                        sample = None
            if isinstance(sample, bool) or not isinstance(sample, int) or sample < 0:
                raise ContainerError("invalid dataset metadata: row does not belong to a sample")
            yield sample, row

    def stage(self, row: dict[str, Any], target: Path) -> Path:
        # A local FOLDER already exposes the final bytes. Archives and remote
        # objects are queued and fetched in bounded batches by flush().
        if self.container == "folder" and isinstance(self.location, Path):
            relative_path = _metadata_path(row, RELATIVE_PATH)
            return self.location / DATA_DIR / relative_path
        self.pending.append((*self._origin(row), target))
        return target

    @property
    def full(self) -> bool:
        # Batch small files to avoid one request per sample.
        return len(self.pending) >= _BATCH_FILES or sum(item[2] for item in self.pending) >= _BATCH_BYTES

    def flush(self) -> None:
        native.fetch(self.pending)
        self.pending.clear()

    def _origin(self, row: dict[str, Any]) -> tuple[str, int, int]:
        if self.container == "folder":
            return _join(self.location, DATA_DIR, _metadata_path(row, RELATIVE_PATH)), 0, 0
        # TACOCAT partitions live beside the catalog.
        archive = (
            fspath(self.location)
            if self.container == "zip"
            else _join(_parent(self.opened.source), _metadata_path(row, SOURCE_FILE))
        )
        return archive, row[OFFSET], row[SIZE]


def _join(location: Location, *parts: str) -> str:
    if isinstance(location, Path):
        return fspath(location.joinpath(*parts))
    return "/".join((location.rstrip("/"), *parts))


def _query(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("sql must be a string")
    value = value.strip()
    if value.endswith(";"):
        value = value[:-1].rstrip()
    if not value:
        raise ValueError("sql must not be empty")
    if "\0" in value:
        raise ValueError("sql must not contain NUL")
    return value


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _parent(location: Location) -> Location:
    if isinstance(location, Path):
        return location.parent
    return location.rstrip("/").rsplit("/", 1)[0]


def _samples(dataset: _Source, selected: set[int] | None, stage: Path) -> Iterator[_PreparedSample]:
    contract = dataset.contract
    nodes: dict[int, dict[str, list[_PreparedNode]]] = {}
    files: dict[int, list[dict[str, Any]]] = {}
    # Rebuild the writer's logical tree from the normalized metadata levels.
    # Child rows are indexed first so each sample can be emitted in source order.
    for level in contract.levels[1:]:
        folder = level_folder(level)
        for key, row in dataset.rows(level, dataset.level(level, selected)):
            name = row[RELATIVE_PATH].rsplit("/", 1)[1]
            is_folder = contract.is_folder(folder, name)
            node = _PreparedNode(name, is_folder, _metadata(contract, level, row))
            nodes.setdefault(key, {}).setdefault(level, []).append(node)
            if not is_folder:
                files.setdefault(key, []).append(row)

    # Preserve source order; each sample is yielded after its files arrive.
    ready: list[_PreparedSample] = []
    for index, (key, row) in enumerate(dataset.rows(SAMPLE_LEVEL, dataset.level(SAMPLE_LEVEL, selected))):
        metadata = _metadata(contract, SAMPLE_LEVEL, row)
        logical_id = row.get(SAMPLE_ID)
        if not isinstance(logical_id, str) or not logical_id.strip():
            raise ContainerError("invalid dataset metadata: id must be a non-empty string")
        assets = []
        for child in files.pop(key, []):
            relative_path = _metadata_path(child, RELATIVE_PATH)
            _, separator, path = relative_path.partition("/")
            if not separator:
                raise ContainerError(f"{RELATIVE_PATH} has no sample prefix: {relative_path!r}")
            assets.append(_PreparedAsset(dataset.stage(child, stage / str(index) / path), path))
        if not assets:
            raise ContainerError(f"invalid dataset metadata: sample {logical_id!r} has no data files")
        levels = nodes.pop(key, {})
        rows = {level: tuple(levels.get(level, ())) for level in contract.levels[1:]}
        ready.append(_PreparedSample(logical_id, tuple(assets), metadata, rows))
        if dataset.full:
            dataset.flush()
            yield from ready
            ready.clear()
    dataset.flush()
    yield from ready


def _metadata_path(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise ContainerError(f"invalid dataset metadata: {field} must be a non-empty string")
    try:
        return normalize_relative_path(value, context=field)
    except ValueError as error:
        raise ContainerError(f"invalid dataset metadata: {error}") from error


def _metadata(contract: Contract, level: str, row: dict[str, Any]) -> dict[str, Any]:
    return {name: row[name] for name in contract.metadata[level]}


__all__ = ["export"]
