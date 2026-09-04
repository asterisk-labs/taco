from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ._parquet import parquet_writer_options
from ._view import DatasetView, open_view
from .contract.collection import Extent
from .contract.naming import COLLECTION_FILENAME, SOURCE_FILE, TACOCAT_DIR, level_to_filename
from .errors import ConsolidationError

__all__ = ["consolidate"]


def _common_parent(paths: Sequence[Path]) -> Path:
    parents = {path.parent for path in paths}
    if len(parents) != 1:
        raise ConsolidationError("partitions live in different directories; pass output= explicitly")
    return parents.pop()


def consolidate(
    archives: Sequence[str | os.PathLike[str]],
    output: str | os.PathLike[str] | None = None,
    *,
    name: str = TACOCAT_DIR,
    overwrite: bool = False,
    row_group_size: int = 65_536,
    parquet_options: Mapping[str, Any] | None = None,
) -> Path:
    """Merge the METADATA of several partitions into ``<output>/.tacocat``.

    Every partition must share the same contract. Each merged Parquet gains
    an ``internal:source_file`` column, and the merged ``COLLECTION.json``
    carries the global extent plus ``taco:sources`` for query routing.
    Returns the path of the ``.tacocat`` directory.
    """
    if not archives:
        raise ConsolidationError("no partitions to consolidate")
    paths = [Path(item).expanduser().resolve() for item in archives]
    if len({path.name for path in paths}) != len(paths):
        raise ConsolidationError("partition file names must be unique")
    directory = Path(output).expanduser().resolve() if output is not None else _common_parent(paths)
    target = directory / name
    if target.exists():
        if not overwrite:
            raise ConsolidationError(f"{target} already exists (set overwrite=True)")
        if not target.is_dir():
            raise ConsolidationError(f"{target} is not a directory")
        for entry in target.iterdir():
            if entry.suffix == ".parquet" or entry.name == COLLECTION_FILENAME:
                entry.unlink()
    target.mkdir(parents=True, exist_ok=True)

    datasets: list[DatasetView] = []
    for path in paths:
        dataset = open_view(path)
        if dataset.container != "zip":
            raise ConsolidationError(f"TACOCAT consolidates archive partitions, got {dataset.container}: {path}")
        datasets.append(dataset)
    reference = datasets[0]
    for dataset in datasets[1:]:
        if dataset.contract != reference.contract:
            raise ConsolidationError(
                f"{dataset.path.name} was built with a different contract than {reference.path.name}"
            )

    writer_options = parquet_writer_options(parquet_options)
    for level in reference.levels:
        tables = []
        for dataset in datasets:
            table = dataset.level(level)
            reference_names = reference.level(level).column_names
            if set(table.column_names) == set(reference_names):
                table = table.select(reference_names)
            if not table.schema.equals(reference.level(level).schema, check_metadata=False):
                raise ConsolidationError(
                    f"level {level!r} in {dataset.path.name} has a different schema than {reference.path.name}"
                )
            source = pa.array([dataset.path.name] * table.num_rows, type=pa.string())
            tables.append(table.append_column(pa.field(SOURCE_FILE, pa.string(), nullable=False), source))
        merged = pa.concat_tables(tables, promote_options="none")
        with pq.ParquetWriter(target / level_to_filename(level), merged.schema, **writer_options) as writer:
            if merged.num_rows:
                writer.write_table(merged, row_group_size=row_group_size)
            else:
                writer.write_table(merged)

    collection = dict(reference.collection_json)
    extents = [dataset.collection.extent for dataset in datasets if dataset.collection.extent is not None]
    merged_extent = Extent.union(extents)
    if merged_extent is not None:
        collection["extent"] = merged_extent.to_dict()
    sources: list[dict[str, Any]] = []
    for dataset in datasets:
        source_entry: dict[str, Any] = {"file": dataset.path.name, "samples": dataset.sample_count}
        if dataset.collection.extent is not None:
            source_entry["spatial"] = list(dataset.collection.extent.spatial)
            if dataset.collection.extent.temporal is not None:
                source_entry["temporal"] = list(dataset.collection.extent.temporal)
        sources.append(source_entry)
    collection["taco:sources"] = {
        "count": len(datasets),
        "files": [dataset.path.name for dataset in datasets],
        "extents": sources,
    }
    (target / COLLECTION_FILENAME).write_text(
        json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
