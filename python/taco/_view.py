from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa

from .contract.collection import Collection
from .contract.contract import COLLECTION_LEVEL, Contract
from .contract.naming import (
    COLLECTION_FILENAME,
    DATA_DIR,
    METADATA_DIR,
    OFFSET,
    RELATIVE_PATH,
    SIZE,
    SOURCE_FILE,
    level_folder,
)
from .errors import CollectionError, ContainerError, ContractError
from .reader import collection as read_collection
from .reader import read

Container = Literal["zip", "folder", "tacocat"]


@dataclass(frozen=True)
class DataRow:
    """One data file as the metadata layer describes it."""

    level: str
    row: int
    relative_path: str
    offset: int | None
    size: int | None
    source_file: str | None = None

    @property
    def archive_name(self) -> str:
        return f"{DATA_DIR}/{self.relative_path}"

    @property
    def sample_index(self) -> int:
        return int(self.relative_path.split("/", 1)[0])


@dataclass
class DatasetView:
    path: Path
    container: Container
    collection: Collection
    collection_json: dict[str, Any]
    tables: dict[str, pa.Table]

    @property
    def contract(self) -> Contract:
        return self.collection.contract

    @property
    def levels(self) -> tuple[str, ...]:
        return self.contract.levels

    @property
    def sample_count(self) -> int:
        return self.tables[COLLECTION_LEVEL].num_rows

    def level(self, name: str) -> pa.Table:
        try:
            return self.tables[name]
        except KeyError as exc:
            raise KeyError(f"level {name!r} is not part of this dataset; levels: {list(self.tables)}") from exc

    def is_leaf_level_row(self, level: str, relative_path: str) -> bool:
        if self.contract.is_null:
            return level == COLLECTION_LEVEL
        if level == COLLECTION_LEVEL:
            return False
        return not self.contract.is_folder(level_folder(level), relative_path.rsplit("/", 1)[-1])

    def iter_data_rows(self) -> Iterator[DataRow]:
        for level in self.levels:
            table = self.tables.get(level)
            if table is None or self.contract.is_null != (level == COLLECTION_LEVEL):
                continue
            columns = [RELATIVE_PATH]
            columns += [name for name in (OFFSET, SIZE, SOURCE_FILE) if name in table.column_names]
            start = 0
            for batch in table.select(columns).to_batches():
                for position, record in enumerate(batch.to_pylist()):
                    relative_path = record[RELATIVE_PATH]
                    if self.is_leaf_level_row(level, relative_path):
                        yield DataRow(
                            level=level,
                            row=start + position,
                            relative_path=relative_path,
                            offset=record.get(OFFSET),
                            size=record.get(SIZE),
                            source_file=record.get(SOURCE_FILE),
                        )
                start += batch.num_rows


def detect_container(path: Path) -> Container:
    if path.is_file():
        return "zip"
    if path.is_dir():
        if not (path / COLLECTION_FILENAME).is_file():
            raise ContainerError(f"{path} has no {COLLECTION_FILENAME}")
        if (path / METADATA_DIR).is_dir():
            return "folder"
        if any(path.glob("*.parquet")):
            return "tacocat"
        raise ContainerError(f"{path} has neither {METADATA_DIR}/ (FOLDER) nor *.parquet (TACOCAT)")
    raise ContainerError(f"dataset not found: {path}")


def open_view(path: str | os.PathLike[str]) -> DatasetView:
    """Assemble the metadata view of a container from the thin reader."""
    location = Path(path).expanduser()
    container = detect_container(location)
    try:
        data = read_collection(location)
        collection = Collection.from_dict(data)
    except (CollectionError, ContractError) as exc:
        raise ContainerError(f"invalid {COLLECTION_FILENAME} in {location}: {exc}") from exc
    except Exception as exc:
        raise ContainerError(f"could not read {COLLECTION_FILENAME} from {location}: {exc}") from exc

    tables: dict[str, pa.Table] = {}
    for level in collection.contract.levels:
        try:
            tables[level] = read(location, level=level)
        except Exception as exc:
            raise ContainerError(f"could not read level {level!r} from {location}: {exc}") from exc
    return DatasetView(location, container, collection, data, tables)
