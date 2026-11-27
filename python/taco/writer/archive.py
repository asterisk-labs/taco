from __future__ import annotations

import os
import pickle
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..container.cozip import cozip_plan, cozip_write
from ..container.publish import publish_file
from ..contract.collection import Collection
from ..contract.contract import SAMPLE_ID, SAMPLE_LEVEL
from ..contract.naming import (
    COLLECTION_FILENAME,
    DATA_DIR,
    METADATA_DIR,
    level_to_filename,
    parse_size,
)
from ..contract.sample import _PreparedSample
from ..errors import WriterError
from .base import BuildResult, Writer, render_collection
from .metadata import MetadataTableWriter
from .progress import Progress

# The Python API requires .zip for a predictable output mode. Readers still
# identify TACO archives from the cozip profile byte, not from this suffix.
_ARCHIVE_SUFFIX = ".zip"


def _normalize_output(output: str | os.PathLike[str]) -> Path:
    path = Path(output).expanduser()
    if path.suffix != _ARCHIVE_SUFFIX:
        raise ValueError("TACO archive output must end in .zip")
    return path.resolve()


def _priority_names(collection: Collection) -> list[str]:
    return [
        COLLECTION_FILENAME,
        *(f"{METADATA_DIR}/{level_to_filename(level)}" for level in collection.contract.levels),
    ]


def _data_entries(sample_index: int, sample: _PreparedSample) -> list[tuple[str, Path, int]]:
    entries: list[tuple[str, Path, int]] = []
    for asset in sample.assets:
        if not isinstance(asset.source, Path):
            raise TypeError("inline assets must be materialized before planning")
        assert asset.path is not None
        assert asset.size is not None
        name = f"{DATA_DIR}/{sample_index}/{asset.path}"
        entries.append((name, asset.source, asset.size))
    return entries


class ArchiveWriter(Writer):
    def __init__(
        self,
        collection: Collection,
        output: str | os.PathLike[str],
        *,
        overwrite: bool = False,
        row_group_size: int = 65_536,
        batch_size: int = 10_000,
        parquet_options: Mapping[str, Any] | None = None,
        partition_size: int | str | None = None,
        partition_by: str | None = None,
        progress: bool = False,
        workers: int = 1,
    ) -> None:
        normalized_output = _normalize_output(output)
        parsed_partition_size = None if partition_size is None else parse_size(partition_size)
        if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
            raise ValueError("workers must be a positive integer")
        if workers > 1 and parsed_partition_size is None and partition_by is None:
            raise ValueError("workers requires a partitioned ZIP dataset")
        if workers > 1:
            try:
                pickle.dumps((collection, dict(parquet_options or {})))
            except Exception as exc:
                raise ValueError(
                    "workers > 1 requires picklable collection and parquet_options; "
                    f"define metadata models and extensions at module level ({exc})"
                ) from exc
        if partition_by == SAMPLE_ID:
            raise ValueError("partition_by cannot be 'id'; it is unique, so every sample would be its own partition")
        if partition_by is not None and partition_by not in collection.contract.metadata[SAMPLE_LEVEL]:
            raise ValueError(
                f"partition_by field {partition_by!r} is not sample metadata; "
                f"available: {list(collection.contract.metadata[SAMPLE_LEVEL])}"
            )
        super().__init__(
            collection,
            batch_size=batch_size,
            row_group_size=row_group_size,
            parquet_options=parquet_options,
            progress=progress,
        )
        self.output = normalized_output
        self.overwrite = overwrite
        self.partition_size = parsed_partition_size
        self.partition_by = partition_by
        self.workers = workers

    def _build(self) -> BuildResult:
        if self.partition_size is None and self.partition_by is None:
            return self._write_single_archive()
        from .partition import write_partitioned

        return write_partitioned(self)

    def _validate_output(self, output: Path) -> None:
        if output.exists():
            if not self.overwrite:
                raise FileExistsError(f"output already exists (set overwrite=True): {output}")
            if not output.is_file():
                raise WriterError(f"output exists and is not a file: {output}")

    def _write_single_archive(self) -> BuildResult:
        self._validate_output(self.output)
        return self._write_archive(
            self.output,
            lambda: ((index, sample) for index, sample, _ in self._staged_samples()),
            self.sample_count,
        )

    def _archive_build(self) -> _ArchiveBuild:
        return _ArchiveBuild(
            collection=self.collection,
            stage=self._stage,
            row_group_size=self.row_group_size,
            batch_size=self.batch_size,
            parquet_options=self.parquet_options,
            overwrite=self.overwrite,
            progress=self.progress,
        )

    def _write_archive(
        self,
        output: Path,
        samples: Callable[[], Iterator[tuple[int, _PreparedSample]]],
        sample_count: int,
        *,
        show_progress: bool = True,
    ) -> BuildResult:
        return self._archive_build().write(output, samples, sample_count, show_progress=show_progress)


@dataclass(frozen=True)
class _ArchiveBuild:
    """What a partition worker needs to write one archive."""

    collection: Collection
    stage: Path
    row_group_size: int
    batch_size: int
    parquet_options: Mapping[str, Any]
    overwrite: bool
    progress: bool

    def write(
        self,
        output: Path,
        samples: Callable[[], Iterator[tuple[int, _PreparedSample]]],
        sample_count: int,
        *,
        show_progress: bool = True,
    ) -> BuildResult:
        contract = self.collection.contract
        with tempfile.TemporaryDirectory(prefix="build-", dir=self.stage) as name:
            stage = Path(name)

            # cozip must know the complete data layout before metadata is
            # written because each file offset becomes a Parquet column. The
            # disk-backed sample stream makes that second pass possible without
            # retaining the whole dataset in memory.
            files: list[tuple[str, Path]] = []
            sizes: list[int] = []
            with self._progress(sample_count, f"planning {output.name}", enabled=show_progress) as progress:
                for index, sample in samples():
                    for name, source, size in _data_entries(index, sample):
                        files.append((name, source))
                        sizes.append(size)
                    progress.update()
            names = _priority_names(self.collection)
            # Sizes come from add(); cozip checks them against the sources
            # when it writes the archive.
            layout = cozip_plan(files, names, sizes=sizes)
            offsets = layout.offsets

            tables = MetadataTableWriter(
                contract,
                stage / METADATA_DIR,
                with_offsets=True,
                parquet_options=self.parquet_options,
                row_group_size=self.row_group_size,
                batch_size=self.batch_size,
            )
            try:
                with self._progress(sample_count, f"metadata {output.name}", enabled=show_progress) as progress:
                    for index, sample in samples():
                        tables.add_sample(index, sample, offsets.__getitem__)
                        progress.update()
                paths = tables.close()
            except BaseException:
                tables.abort()
                raise

            collection_path = stage / COLLECTION_FILENAME
            collection_path.write_text(render_collection(self.collection, tables.summaries), encoding="utf-8")

            # Collection and metadata members are placed in cozip's priority
            # area so readers can fetch them with a small number of range reads.
            priority_files = [(COLLECTION_FILENAME, collection_path)]
            priority_files += [
                (f"{METADATA_DIR}/{level_to_filename(level)}", paths[level]) for level in contract.levels
            ]

            output.parent.mkdir(parents=True, exist_ok=True)
            # cozip creates the archive in a private directory beside the
            # output, so it gets the permissions of a new file under the umask.
            with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=output.parent) as packing:
                temporary_output = Path(packing) / output.name
                with self._progress(1, f"packing {output.name}", enabled=show_progress, unit="archive") as progress:
                    cozip_write(temporary_output, layout, priority_files)
                    progress.update()

                # The completed temporary archive is renamed into place only
                # after cozip_write returns, so readers never see half a ZIP.
                publish_file(temporary_output, output, overwrite=self.overwrite)
            return BuildResult(
                path=output,
                samples=sample_count,
                data_files=len(files),
                metadata_files=len(contract.levels),
                size=output.stat().st_size,
            )

    def _progress(self, total: int, description: str, *, enabled: bool, unit: str = "sample") -> Progress:
        return Progress(self.progress and enabled, total, description, unit)


__all__ = ["ArchiveWriter"]
