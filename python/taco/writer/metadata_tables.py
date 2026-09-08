from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .._parquet import parquet_writer_options
from ..contract.contract import SAMPLE_LEVEL, Contract
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
from ..contract.sample import _PreparedSample
from ..metadata._base import CollectionSummary


# Summary reducers refer to fields without a namespace. Keep the namespace
# beside each reducer so it reads the correct Parquet columns.
@dataclass
class _SummaryAccumulator:
    field: str
    level: str
    namespace: str
    reducer: CollectionSummary

    def update_rows(self, rows: list[dict[str, Any]]) -> None:
        columns = {name: [row.get(f"{self.namespace}:{name}") for row in rows] for name in self.reducer.requires}
        self.reducer.update(columns)

    def update_table(self, table: pa.Table) -> None:
        columns = {name: table.column(f"{self.namespace}:{name}").to_pylist() for name in self.reducer.requires}
        self.reducer.update(columns)


def _collection_summaries(contract: Contract) -> list[_SummaryAccumulator]:
    summaries = []
    fields_seen = set()
    for level in contract.levels:
        for group in contract._groups[level]:
            for summary in group.summaries:
                # COLLECTION.json has one value per summary name. If the same
                # profile appears at several levels, the first level owns it.
                if summary.field in fields_seen:
                    continue
                summaries.append(_SummaryAccumulator(summary.field, level, group.namespace, summary()))
                fields_seen.add(summary.field)
    return summaries


def internal_columns_for(contract: Contract, level: str, *, with_offsets: bool) -> list[str]:
    columns = [CURRENT_ID]
    if level != SAMPLE_LEVEL:
        columns.append(PARENT_ID)
    columns.append(RELATIVE_PATH)
    if with_offsets and (level != SAMPLE_LEVEL or contract.is_null):
        columns.extend((OFFSET, SIZE))
    return columns


def table_schema(contract: Contract, level: str, *, with_offsets: bool) -> pa.Schema:
    fields: list[pa.Field[Any]] = []
    for name in internal_columns_for(contract, level, with_offsets=with_offsets):
        if name in (OFFSET, SIZE):
            fields.append(pa.field(name, OFFSET_TYPE, nullable=True))
        elif name == RELATIVE_PATH:
            fields.append(pa.field(name, pa.string(), nullable=False))
        else:
            fields.append(pa.field(name, ID_TYPE, nullable=False))
    for name, spec in contract.metadata[level].items():
        metadata = {b"description": spec.description.encode()} if spec.description else None
        fields.append(pa.field(name, contract.arrow_types(level)[name], nullable=spec.nullable, metadata=metadata))
    return pa.schema(fields, metadata={b"taco:level": level.encode()})


class MetadataTableWriter:
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
        self._schemas = {level: table_schema(contract, level, with_offsets=with_offsets) for level in contract.levels}

        # Rows from one sample can land in several levels. Each level keeps its
        # own buffer, id counter and Parquet writer.
        self._buffers: dict[str, list[dict[str, Any]]] = {level: [] for level in contract.levels}
        self._writers: dict[str, pq.ParquetWriter] = {}
        self._next_id = dict.fromkeys(contract.levels, 0)
        self._verified_derived: set[str] = set()
        self._summaries = _collection_summaries(contract)
        self.paths = {level: directory / level_to_filename(level) for level in contract.levels}
        directory.mkdir(parents=True, exist_ok=True)

    def write_existing(self, level: str, table: pa.Table) -> None:
        # Once new ids have been assigned, prepending old rows would invalidate
        # parent links. Append mode therefore calls this before add_sample().
        if self._next_id[level] or self._buffers[level]:
            raise RuntimeError("existing rows must be written first")
        table = table.select(self._schemas[level].names).cast(self._schemas[level])
        for summary in self._summaries:
            if summary.level == level:
                summary.update_table(table)
        if table.num_rows:
            self._parquet_writer(level).write_table(table, row_group_size=self.row_group_size)
        self._next_id[level] = table.num_rows

    def add_sample(
        self,
        sample_id: int,
        sample: _PreparedSample,
        locate: Callable[[str], tuple[int, int]] | None = None,
    ) -> None:
        # Folder datasets locate data by path. ZIP datasets also store byte
        # offsets, supplied here from the layout produced by cozip.
        if self.with_offsets and locate is None:
            raise RuntimeError("offsets requested without a locator")

        sample_row: dict[str, Any] = {CURRENT_ID: sample_id, RELATIVE_PATH: str(sample_id)}
        if self.contract.is_null and self.with_offsets:
            assert locate is not None
            sample_row[OFFSET], sample_row[SIZE] = locate(f"{DATA_DIR}/{sample_id}")
        sample_row.update(sample.metadata)
        self._append_row(SAMPLE_LEVEL, sample_row)
        if self.contract.is_null:
            return

        # Folder ids are local to a metadata level. Remember them by path while
        # this sample is expanded so child rows can point at the right parent.
        folder_ids: dict[tuple[str, ...], int] = {(): sample_id}
        for level in self.contract.levels[1:]:
            folder = level_folder(level)
            parent_id = folder_ids[folder]
            for node in sample.rows[level]:
                row_id = self._next_id[level]
                relative_path = f"{sample_id}/{'/'.join((*folder, node.name))}"
                row: dict[str, Any] = {
                    CURRENT_ID: row_id,
                    PARENT_ID: parent_id,
                    RELATIVE_PATH: relative_path,
                }
                if node.is_folder:
                    folder_ids[(*folder, node.name)] = row_id
                    if self.with_offsets:
                        row[OFFSET] = None
                        row[SIZE] = None
                elif self.with_offsets:
                    assert locate is not None
                    row[OFFSET], row[SIZE] = locate(f"{DATA_DIR}/{relative_path}")
                row.update(node.metadata)
                self._append_row(level, row)

    def _append_row(self, level: str, row: dict[str, Any]) -> None:
        self._buffers[level].append(row)
        self._next_id[level] += 1
        if len(self._buffers[level]) >= self.batch_size:
            self._flush(level)

    def _parquet_writer(self, level: str) -> pq.ParquetWriter:
        writer = self._writers.get(level)
        if writer is None:
            writer = pq.ParquetWriter(self.paths[level], self._schemas[level], **self._writer_options)
            self._writers[level] = writer
        return writer

    def _flush(self, level: str) -> None:
        rows = self._buffers[level]
        if not rows:
            return

        # Derived metadata must be row-local; otherwise changing batch_size
        # would change the dataset. Check that once, on the first useful batch,
        # because the verification computes the derived values again per row.
        verify = level not in self._verified_derived
        self.contract.apply_derived(level, rows, verify=verify)
        self._verified_derived.add(level)
        for summary in self._summaries:
            if summary.level == level:
                summary.update_rows(rows)
        table = pa.Table.from_pylist(rows, schema=self._schemas[level])
        self._parquet_writer(level).write_table(table, row_group_size=self.row_group_size)
        self._buffers[level] = []

    def close(self) -> dict[str, Path]:
        for level in self.contract.levels:
            self._flush(level)
            writer = self._parquet_writer(level)
            if self._next_id[level] == 0:
                # The file must still exist for an empty level, with enough
                # schema information for readers to validate it.
                writer.write_table(self._schemas[level].empty_table())
        for writer in self._writers.values():
            writer.close()
        self._writers = {}
        return dict(self.paths)

    @property
    def summaries(self) -> dict[str, Any]:
        return {summary.field: summary.reducer.finish() for summary in self._summaries}

    def abort(self) -> None:
        # Cleanup must not replace the exception that caused the build to fail.
        for writer in self._writers.values():
            with contextlib.suppress(Exception):
                writer.close()
        self._writers = {}
        for summary in self._summaries:
            summary.reducer.close()


__all__ = ["MetadataTableWriter", "internal_columns_for", "table_schema"]
