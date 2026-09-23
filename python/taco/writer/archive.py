from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
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
from .base import BuildResult, Writer
from .metadata import MetadataTableWriter

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


def _data_entries(sample_index: int, sample: _PreparedSample) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for asset in sample.assets:
        if not isinstance(asset.source, Path):
            raise TypeError("inline assets must be materialized before planning")
        assert asset.path is not None
        name = f"{DATA_DIR}/{sample_index}/{asset.path}"
        entries.append((name, asset.source))
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
        if partition_size is not None and partition_by is not None:
            raise ValueError("use either partition_size or partition_by, not both")
        parsed_partition_size = None if partition_size is None else parse_size(partition_size)
        if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
            raise ValueError("workers must be a positive integer")
        if workers > 1 and parsed_partition_size is None and partition_by is None:
            raise ValueError("workers requires a partitioned ZIP dataset")
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

    def _write_archive(
        self,
        output: Path,
        samples: Callable[[], Iterator[tuple[int, _PreparedSample]]],
        sample_count: int,
        *,
        show_progress: bool = True,
    ) -> BuildResult:
        temporary_output: Path | None = None
        with tempfile.TemporaryDirectory(prefix="build-", dir=self._stage) as name:
            stage = Path(name)

            # cozip must know the complete data layout before metadata is
            # written because each file offset becomes a Parquet column. The
            # disk-backed sample stream makes that second pass possible without
            # retaining the whole dataset in memory.
            files: list[tuple[str, Path]] = []
            with self._show_progress(sample_count, f"planning {output.name}", enabled=show_progress) as progress:
                for index, sample in samples():
                    files.extend(_data_entries(index, sample))
                    progress.update()
            names = _priority_names(self.collection)
            layout = cozip_plan(files, names)
            offsets = layout.offsets

            tables = MetadataTableWriter(
                self.contract,
                stage / METADATA_DIR,
                with_offsets=True,
                parquet_options=self.parquet_options,
                row_group_size=self.row_group_size,
                batch_size=self.batch_size,
            )
            try:
                with self._show_progress(sample_count, f"metadata {output.name}", enabled=show_progress) as progress:
                    for index, sample in samples():
                        tables.add_sample(index, sample, offsets.__getitem__)
                        progress.update()
                paths = tables.close()
            except BaseException:
                tables.abort()
                raise

            collection_path = stage / COLLECTION_FILENAME
            collection_path.write_text(self._render_collection(tables.summaries), encoding="utf-8")

            # Collection and metadata members are placed in cozip's priority
            # area so readers can fetch them with a small number of range reads.
            priority_files = [(COLLECTION_FILENAME, collection_path)]
            priority_files += [
                (f"{METADATA_DIR}/{level_to_filename(level)}", paths[level]) for level in self.contract.levels
            ]

            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
            os.close(descriptor)
            temporary_output = Path(temporary_name)
            try:
                with self._show_progress(1, f"packing {output.name}", "archive", enabled=show_progress) as progress:
                    cozip_write(temporary_output, layout, priority_files)
                    progress.update()
                # mkstemp creates 0600; a published archive follows the umask.
                umask = os.umask(0)
                os.umask(umask)
                temporary_output.chmod(0o666 & ~umask)

                # The completed temporary archive is renamed into place only
                # after cozip_write returns, so readers never see half a ZIP.
                publish_file(temporary_output, output, overwrite=self.overwrite)
                temporary_output = None
                return BuildResult(
                    path=output,
                    samples=sample_count,
                    data_files=len(files),
                    metadata_files=len(self.contract.levels),
                    size=output.stat().st_size,
                )
            finally:
                if temporary_output is not None:
                    temporary_output.unlink(missing_ok=True)


__all__ = ["ArchiveWriter"]
