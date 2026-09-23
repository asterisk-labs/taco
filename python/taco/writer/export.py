from __future__ import annotations

import json
from collections.abc import Iterator
from os import PathLike, fspath
from pathlib import Path
from typing import Any, TypeAlias

import pyarrow as pa

from ..contract.collection import Collection
from ..contract.contract import SAMPLE_ID, SAMPLE_LEVEL, Contract
from ..contract.naming import (
    DATA_DIR,
    OFFSET,
    RELATIVE_PATH,
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

# Sample ids restart in every TACOCAT partition, so the partition is part of
# the key. It is None for FOLDER and ZIP datasets.
SampleKey: TypeAlias = tuple[str | None, int]

_BATCH_FILES = 256
_BATCH_BYTES = 256 * 1024 * 1024


def export(
    source: Source | Dataset,
    output: str | PathLike[str],
    *,
    samples: Any = None,
    overwrite: bool = False,
    **fields: Any,
) -> BuildResult:
    """Copy a dataset or selected samples to a new TACO output.

    ``samples`` is an Arrow-compatible table with ``sample_id`` and, for a
    TACOCAT, ``source_file``. Other keyword arguments replace collection
    fields. The export keeps the contract, renumbers samples, and recomputes
    the extent.
    """
    sources = source.sources if isinstance(source, Dataset) else normalize_sources(source)
    if len(sources) != 1:
        raise ValueError("export reads one dataset")
    if "contract" in fields or "sources" in fields:
        raise ValueError("export keeps the contract and drops taco:sources")
    dataset = _Source(sources[0])
    selected = None if samples is None else dataset.select(samples)
    # Validate the complete selection before fetching payload bytes. A typo in
    # a sample id therefore cannot leave a partially populated writer stage.
    sample_rows = dataset.level(SAMPLE_LEVEL, selected)
    if selected is not None:
        dataset.validate_selection(selected, sample_rows)
    collection = dataset.collection.replace(sources=None, **fields)

    with open_writer(collection, output, overwrite=overwrite, progress=True) as writer:
        total = sample_rows.num_rows
        with Progress(True, total, f"exporting {dataset.name}") as bar:
            for sample in _samples(dataset, selected, writer._stage / "export"):
                writer._add_prepared(sample)
                bar.update()
        return writer.run()


class _Source:
    def __init__(self, location: Location) -> None:
        self.location = location
        # Reuse the native handle; opening a remote source costs round trips.
        self.opened = native.NativeDataset(location)
        self.container = self.opened.container
        self.collection = Collection.from_dict(json.loads(self.opened.collection))
        self.levels: dict[str, pa.Table] = {}
        self.pending: list[tuple[str, int, int, Path]] = []

    @property
    def name(self) -> str:
        return self.location.name if isinstance(self.location, Path) else self.location.rstrip("/").rsplit("/", 1)[-1]

    @property
    def contract(self) -> Contract:
        return self.collection.contract

    def level(self, name: str, selected: set[SampleKey] | None) -> pa.Table:
        if name not in self.levels:
            sql = native.sql([self.opened], idx=None, level=name, pivoted=True, files=None, location=False)
            connection = engine.open_reader()
            if selected is None:
                self.levels[name] = connection.execute(sql).to_arrow_table()
            else:
                # DuckDB filters while it scans the cached Parquet, before rows
                # become Python objects, and preserves the source order.
                keys = pa.table(
                    {
                        "partition": pa.array([partition for partition, _ in selected], pa.string()),
                        "sample": pa.array([sample for _, sample in selected], pa.int64()),
                    }
                )
                partition = f'rows."{SOURCE_FILE}"' if self.container == "tacocat" else "NULL"
                connection.register("export_keys", keys)
                try:
                    self.levels[name] = connection.execute(
                        f"""
                        SELECT rows.* EXCLUDE (ordinal)
                        FROM (SELECT *, row_number() OVER () AS ordinal FROM ({sql})) AS rows
                        SEMI JOIN export_keys AS keys
                          ON CAST(split_part(rows."{RELATIVE_PATH}", '/', 1) AS BIGINT) = keys.sample
                         AND {partition} IS NOT DISTINCT FROM keys.partition
                        ORDER BY ordinal
                        """
                    ).to_arrow_table()
                finally:
                    connection.unregister("export_keys")
        return self.levels[name]

    def select(self, samples: Any) -> set[SampleKey]:
        # Accept any table that implements the Arrow C stream protocol.
        table = pa.table(samples)
        if "sample_id" not in table.column_names:
            raise ValueError("samples must contain a sample_id column")
        ids = table.column("sample_id").to_pylist()
        if any(isinstance(sample, bool) or not isinstance(sample, int) or sample < 0 for sample in ids):
            raise ValueError("samples must contain non-negative integer sample_id values")
        if self.container != "tacocat":
            return {(None, sample) for sample in ids}
        if "source_file" not in table.column_names:
            raise ValueError("samples of a TACOCAT must keep the source_file column")
        partitions = [
            _metadata_path({SOURCE_FILE: value}, SOURCE_FILE) for value in table.column("source_file").to_pylist()
        ]
        return set(zip(partitions, ids, strict=True))

    def validate_selection(self, selected: set[SampleKey], rows: pa.Table) -> None:
        found = {key for key, _ in _rows(rows)}
        missing = selected - found
        if not missing:
            return
        ordered = sorted(missing, key=lambda key: (key[0] or "", key[1]))
        if self.container == "tacocat":
            preview = ", ".join(f"{partition}:{sample}" for partition, sample in ordered[:3])
        else:
            preview = ", ".join(str(sample) for _, sample in ordered[:3])
        suffix = "" if len(missing) <= 3 else ", ..."
        noun = "sample" if len(missing) == 1 else "samples"
        raise ValueError(f"{noun} not found in the source: {preview}{suffix}")

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
            else _join(_parent(self.location), _metadata_path(row, SOURCE_FILE))
        )
        return archive, row[OFFSET], row[SIZE]


def _join(location: Location, *parts: str) -> str:
    if isinstance(location, Path):
        return fspath(location.joinpath(*parts))
    return "/".join((location.rstrip("/"), *parts))


def _parent(location: Location) -> Location:
    if isinstance(location, Path):
        return location.parent
    return location.rstrip("/").rsplit("/", 1)[0]


def _samples(dataset: _Source, selected: set[SampleKey] | None, stage: Path) -> Iterator[_PreparedSample]:
    contract = dataset.contract
    nodes: dict[SampleKey, dict[str, list[_PreparedNode]]] = {}
    files: dict[SampleKey, list[dict[str, Any]]] = {}
    # Rebuild the writer's logical tree from the normalized metadata levels.
    # Child rows are indexed first so each sample can be emitted in source order.
    for level in contract.levels[1:]:
        folder = level_folder(level)
        for key, row in _rows(dataset.level(level, selected)):
            name = row[RELATIVE_PATH].rsplit("/", 1)[1]
            is_folder = contract.is_folder(folder, name)
            node = _PreparedNode(name, is_folder, _metadata(contract, level, row))
            nodes.setdefault(key, {}).setdefault(level, []).append(node)
            if not is_folder:
                files.setdefault(key, []).append(row)

    # Preserve source order; each sample is yielded after its files arrive.
    ready: list[_PreparedSample] = []
    for index, (key, row) in enumerate(_rows(dataset.level(SAMPLE_LEVEL, selected))):
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
        levels = nodes.pop(key, {})
        rows = {level: tuple(levels.get(level, ())) for level in contract.levels[1:]}
        ready.append(_PreparedSample(logical_id, tuple(assets), metadata, rows))
        if dataset.full:
            dataset.flush()
            yield from ready
            ready.clear()
    dataset.flush()
    yield from ready


def _rows(table: pa.Table) -> Iterator[tuple[SampleKey, dict[str, Any]]]:
    for row in table.to_pylist():
        relative_path = _metadata_path(row, RELATIVE_PATH)
        prefix = relative_path.split("/", 1)[0]
        try:
            sample = int(prefix)
        except ValueError:
            raise ContainerError(f"{RELATIVE_PATH} must start with a sample index: {relative_path!r}") from None
        if sample < 0:
            raise ContainerError(f"{RELATIVE_PATH} must start with a non-negative sample index: {relative_path!r}")
        source_file = row.get(SOURCE_FILE)
        if source_file is not None:
            source_file = _metadata_path(row, SOURCE_FILE)
        yield (source_file, sample), row


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
