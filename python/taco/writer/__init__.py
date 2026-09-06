from __future__ import annotations

from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from ..contract.collection import Collection
from ._base import _Writer
from .archive import _open_archive
from .folder import _open_folder_writer


def open_writer(
    collection: Collection,
    output: str | PathLike[str],
    *,
    append: bool = False,
    overwrite: bool = False,
    link: bool = False,
    row_group_size: int = 65_536,
    batch_size: int = 10_000,
    parquet_options: Mapping[str, Any] | None = None,
    partition_size: int | str | None = None,
    partition_by: str | None = None,
) -> _Writer:
    if collection.sources is not None:
        raise ValueError("taco:sources is reserved for TACOCAT")
    path = Path(output).expanduser()
    if path.suffix == ".zip":
        if append:
            raise ValueError("ZIP datasets are immutable")
        if link:
            raise ValueError("link is only valid for FOLDER datasets")
        return _open_archive(
            collection,
            path,
            overwrite=overwrite,
            row_group_size=row_group_size,
            batch_size=batch_size,
            parquet_options=parquet_options,
            partition_size=partition_size,
            partition_by=partition_by,
        )
    if path.suffix:
        raise ValueError("output must end in .zip or have no suffix")
    if partition_size is not None or partition_by is not None:
        raise ValueError("partitioning is only valid for ZIP datasets")
    return _open_folder_writer(
        collection,
        path,
        append=append,
        overwrite=overwrite,
        link=link,
        row_group_size=row_group_size,
        batch_size=batch_size,
        parquet_options=parquet_options,
    )


__all__ = ["open_writer"]
