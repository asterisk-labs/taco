from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from os import PathLike
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ._parquet import parquet_writer_options
from ._publish import publish_many
from ._view import DatasetView, open_view
from .contract.collection import Extent
from .contract.naming import COLLECTION_FILENAME, SOURCE_FILE, TACOCAT_DIR, level_to_filename, validate_component
from .errors import ConsolidationError, ContractError
from .writer.metadata_tables import table_schema

__all__ = ["consolidate"]


def _common_parent(paths: Sequence[Path]) -> Path:
    parents = {path.parent for path in paths}
    if len(parents) != 1:
        raise ConsolidationError("partitions live in different directories; pass output= explicitly")
    return parents.pop()


def _check_partition(dataset: DatasetView, reference: DatasetView) -> None:
    if dataset.container != "zip":
        raise ConsolidationError(f"TACOCAT consolidates archive partitions, got {dataset.container}: {dataset.path}")
    if dataset.contract != reference.contract:
        raise ConsolidationError(f"{dataset.path.name} was built with a different contract than {reference.path.name}")
    if _collection_metadata(dataset) != _collection_metadata(reference):
        raise ConsolidationError(f"{dataset.path.name} has different collection metadata than {reference.path.name}")


def _collection_metadata(dataset: DatasetView) -> dict[str, Any]:
    metadata = dict(dataset.collection_json)
    metadata.pop("extent", None)
    metadata.pop("taco:sources", None)
    return metadata


def _ordered_table(dataset: DatasetView, reference: DatasetView, level: str) -> pa.Table:
    table = dataset.level(level)
    expected = reference.level(level)
    if set(table.column_names) == set(expected.column_names):
        table = table.select(expected.column_names)
    if not table.schema.equals(expected.schema, check_metadata=False):
        raise ConsolidationError(
            f"level {level!r} in {dataset.path.name} has a different schema than {reference.path.name}"
        )
    return table


def _source_entry(dataset: DatasetView, directory: Path) -> dict[str, Any]:
    source = Path(os.path.relpath(dataset.path, directory)).as_posix()
    entry: dict[str, Any] = {"file": source, "samples": dataset.sample_count}
    extent = dataset.collection.extent
    if extent is not None:
        entry["spatial"] = list(extent.spatial)
        if extent.temporal is not None:
            entry["temporal"] = list(extent.temporal)
    return entry


def consolidate(
    archives: Sequence[str | PathLike[str]],
    output: str | PathLike[str] | None = None,
    *,
    name: str = TACOCAT_DIR,
    overwrite: bool = False,
    row_group_size: int = 65_536,
    parquet_options: Mapping[str, Any] | None = None,
) -> Path:
    """Merge the metadata of several archive partitions into a TACOCAT."""
    if not archives:
        raise ConsolidationError("no partitions to consolidate")
    if not isinstance(name, str) or "/" in name or "\\" in name:
        raise ConsolidationError("name must be one portable directory name")
    try:
        validate_component(name, context="TACOCAT directory")
    except ContractError as exc:
        raise ConsolidationError("name must be one portable directory name") from exc
    if row_group_size < 1:
        raise ValueError("row_group_size must be positive")

    paths = [Path(item).expanduser().resolve() for item in archives]
    directory = Path(output).expanduser().resolve() if output is not None else _common_parent(paths)
    source_names = [Path(os.path.relpath(path, directory)).as_posix() for path in paths]
    if len(source_names) != len(set(source_names)):
        raise ConsolidationError("partition paths must be unique")
    target = directory / name
    if target.exists():
        if not overwrite:
            raise ConsolidationError(f"{target} already exists (set overwrite=True)")
        if not target.is_dir():
            raise ConsolidationError(f"{target} is not a directory")

    reference = open_view(paths[0])
    _check_partition(reference, reference)
    writer_options = parquet_writer_options(parquet_options)
    output_schemas = {
        level: table_schema(reference.contract, level, with_offsets=True).append(
            pa.field(SOURCE_FILE, pa.string(), nullable=False)
        )
        for level in reference.levels
    }
    collection = dict(reference.collection_json)
    extents: list[Extent] = []
    sources: list[dict[str, Any]] = []

    directory.mkdir(parents=True, exist_ok=True)
    prefix = f".{name.lstrip('.') or 'tacocat'}-build-"
    with tempfile.TemporaryDirectory(prefix=prefix, dir=directory) as temporary:
        build = Path(temporary) / name
        build.mkdir()
        with ExitStack() as stack:
            writers = {
                level: stack.enter_context(pq.ParquetWriter(build / level_to_filename(level), schema, **writer_options))
                for level, schema in output_schemas.items()
            }
            for index, path in enumerate(paths):
                dataset = reference if index == 0 else open_view(path)
                _check_partition(dataset, reference)
                if dataset.collection.extent is not None:
                    extents.append(dataset.collection.extent)
                source_entry = _source_entry(dataset, directory)
                sources.append(source_entry)
                for level in reference.levels:
                    table = _ordered_table(dataset, reference, level)
                    source = pa.array([source_entry["file"]] * table.num_rows, type=pa.string())
                    table = table.append_column(output_schemas[level].field(SOURCE_FILE), source)
                    table = pa.Table.from_arrays(table.columns, schema=output_schemas[level])
                    if table.num_rows:
                        writers[level].write_table(table, row_group_size=row_group_size)
                    else:
                        writers[level].write_table(table)

        merged_extent = Extent.union(extents)
        if merged_extent is not None:
            collection["extent"] = merged_extent.to_dict()
        collection["taco:sources"] = {
            "samples": sum(entry["samples"] for entry in sources),
            "partitions": sources,
        }
        (build / COLLECTION_FILENAME).write_text(
            json.dumps(collection, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        publish_many([(build, target)], overwrite=overwrite)
    return target
