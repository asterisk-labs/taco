from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .._parquet import parquet_writer_options
from ..contract.contract import COLLECTION_LEVEL, Contract
from ..contract.naming import (
    CURRENT_ID,
    DATA_DIR,
    ID_TYPE,
    OFFSET,
    OFFSET_TYPE,
    PARENT_ID,
    RELATIVE_PATH,
    SIZE,
    level_folder,
    level_to_filename,
)
from ..contract.sample import Sample

__all__ = ["LevelTableWriter", "internal_columns", "level_schema"]


def internal_columns(contract: Contract, level: str, *, with_offsets: bool) -> list[str]:
    """Ordered internal column names for ``level`` (spec section 7.2)."""
    if level == COLLECTION_LEVEL:
        columns = [CURRENT_ID, RELATIVE_PATH]
        if with_offsets and contract.is_null:
            columns += [OFFSET, SIZE]
        return columns
    columns = [CURRENT_ID, PARENT_ID, RELATIVE_PATH]
    if with_offsets:
        columns += [OFFSET, SIZE]
    return columns


def level_schema(contract: Contract, level: str, *, with_offsets: bool) -> pa.Schema:
    fields: list[pa.Field] = []
    for name in internal_columns(contract, level, with_offsets=with_offsets):
        if name in (OFFSET, SIZE):
            fields.append(pa.field(name, OFFSET_TYPE, nullable=True))
        elif name == RELATIVE_PATH:
            fields.append(pa.field(name, pa.string(), nullable=False))
        else:
            fields.append(pa.field(name, ID_TYPE, nullable=False))
    types = contract.arrow_types(level)
    for name, (_, description) in contract.metadata[level].items():
        metadata = {b"description": description.encode("utf-8")} if description else None
        fields.append(pa.field(name, types[name], metadata=metadata))
    return pa.schema(fields, metadata={b"taco:level": level.encode("utf-8")})


class LevelTableWriter:
    """Write ``METADATA/<level>.parquet`` files while samples stream by.

    Rows are buffered per level and flushed in batches, so memory stays
    bounded regardless of the number of samples. Row ids are assigned in
    order of arrival, which is what makes ``internal:current_id`` equal the
    row position and ``internal:parent_id`` a valid foreign key.
    """

    def __init__(
        self,
        contract: Contract,
        directory: Path,
        *,
        with_offsets: bool,
        parquet_options: Mapping[str, Any] | None = None,
        row_group_size: int = 65_536,
        batch_size: int = 10_000,
    ) -> None:
        self.contract = contract
        self.directory = directory
        self.with_offsets = with_offsets
        self.row_group_size = row_group_size
        self.batch_size = batch_size
        self._writer_options = parquet_writer_options(parquet_options)
        self._schemas = {level: level_schema(contract, level, with_offsets=with_offsets) for level in contract.levels}
        self._buffers: dict[str, list[dict[str, Any]]] = {level: [] for level in contract.levels}
        self._writers: dict[str, pq.ParquetWriter] = {}
        self._next_id: dict[str, int] = {level: 0 for level in contract.levels}
        self.paths: dict[str, Path] = {level: directory / level_to_filename(level) for level in contract.levels}
        directory.mkdir(parents=True, exist_ok=True)

    def write_existing(self, level: str, table: pa.Table) -> None:
        """Seed a level with already-written rows (FOLDER append)."""
        if self._next_id[level] != 0 or self._buffers[level]:
            raise RuntimeError("write_existing must run before any new row")
        table = table.select(self._schemas[level].names).cast(self._schemas[level])
        writer = self._writer(level)
        if table.num_rows:
            writer.write_table(table, row_group_size=self.row_group_size)
        self._next_id[level] = table.num_rows

    def add_sample(
        self,
        sample_index: int,
        sample: Sample,
        locate: Callable[[str], tuple[int, int]] | None = None,
    ) -> None:
        """Append the rows of one validated sample at every level."""
        contract = self.contract
        if self.with_offsets and locate is None:
            raise RuntimeError("offsets requested but no locate() function was given")

        collection_row: dict[str, Any] = {
            CURRENT_ID: sample_index,
            RELATIVE_PATH: str(sample_index),
        }
        if contract.is_null and self.with_offsets:
            offset, size = locate(f"{DATA_DIR}/{sample_index}")  # type: ignore[misc]
            collection_row[OFFSET] = offset
            collection_row[SIZE] = size
        collection_row.update(sample.metadata[COLLECTION_LEVEL])
        self._push(COLLECTION_LEVEL, collection_row)

        if contract.is_null:
            return

        tree = contract.expand(sample.assets)
        node_ids: dict[tuple[str, ...], int] = {(): sample_index}
        for level in contract.levels[1:]:
            folder = level_folder(level)
            parent_id = node_ids[folder]
            level_metadata = sample.metadata[level]
            for node in tree[folder]:
                row_id = self._next_id[level]
                relative_path = f"{sample_index}/{'/'.join((*folder, node.name))}"
                row: dict[str, Any] = {
                    CURRENT_ID: row_id,
                    PARENT_ID: parent_id,
                    RELATIVE_PATH: relative_path,
                }
                if node.is_folder:
                    node_ids[(*folder, node.name)] = row_id
                    if self.with_offsets:
                        row[OFFSET] = None
                        row[SIZE] = None
                elif self.with_offsets:
                    offset, size = locate(f"{DATA_DIR}/{relative_path}")  # type: ignore[misc]
                    row[OFFSET] = offset
                    row[SIZE] = size
                row.update(level_metadata[node.name])
                self._push(level, row)

    def _push(self, level: str, row: dict[str, Any]) -> None:
        self._buffers[level].append(row)
        self._next_id[level] += 1
        if len(self._buffers[level]) >= self.batch_size:
            self._flush(level)

    def _writer(self, level: str) -> pq.ParquetWriter:
        writer = self._writers.get(level)
        if writer is None:
            writer = pq.ParquetWriter(self.paths[level], self._schemas[level], **self._writer_options)
            self._writers[level] = writer
        return writer

    def _flush(self, level: str) -> None:
        rows = self._buffers[level]
        if not rows:
            return
        table = pa.Table.from_pylist(rows, schema=self._schemas[level])
        self._writer(level).write_table(table, row_group_size=self.row_group_size)
        self._buffers[level] = []

    def close(self) -> dict[str, Path]:
        """Flush every level (creating empty files where needed) and return paths."""
        for level in self.contract.levels:
            self._flush(level)
            writer = self._writer(level)
            if self._next_id[level] == 0:
                writer.write_table(self._schemas[level].empty_table())
        for writer in self._writers.values():
            writer.close()
        self._writers = {}
        return dict(self.paths)

    def abort(self) -> None:
        for writer in self._writers.values():
            with contextlib.suppress(Exception):  # best effort
                writer.close()
        self._writers = {}
