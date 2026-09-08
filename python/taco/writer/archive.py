from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .._publish import publish_file, publish_many
from ..contract.collection import Collection
from ..contract.contract import SAMPLE_LEVEL
from ..contract.naming import (
    COLLECTION_FILENAME,
    DATA_DIR,
    METADATA_DIR,
    level_to_filename,
    parse_size,
    sanitize_filename,
)
from ..contract.sample import _PreparedSample
from ..cozip import cozip_plan, cozip_write
from ..errors import WriterError
from .core import BuildResult, Writer
from .metadata_tables import MetadataTableWriter
from .staging import StagedSamples

logger = logging.getLogger("taco")

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
        name = f"{DATA_DIR}/{sample_index}" if asset.path is None else f"{DATA_DIR}/{sample_index}/{asset.path}"
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
        return self._write_partitioned_archives()

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

    def _stage_partitions(self) -> list[tuple[str, StagedSamples[tuple[_PreparedSample, int]]]]:
        directory = self._stage / "partitions"
        directory.mkdir()
        partitions: list[tuple[str, StagedSamples[tuple[_PreparedSample, int]]]] = []
        labels: dict[str, Any] = {}

        try:
            if self.partition_by is not None:
                streams: dict[str, StagedSamples[tuple[_PreparedSample, int]]] = {}
                for sample, size in self._samples_with_partition_metadata():
                    value = sample.metadata[self.partition_by]
                    label = sanitize_filename(str(value))

                    # Sanitizing can map distinct values to the same filename.
                    # Failing here is better than silently mixing both groups.
                    if label in labels and labels[label] != value:
                        raise WriterError(
                            f"partition values {labels[label]!r} and {value!r} collide on file name {label!r}"
                        )
                    labels[label] = value
                    if label not in streams:
                        stream: StagedSamples[tuple[_PreparedSample, int]] = StagedSamples(
                            directory / f"{len(streams)}.stage"
                        )
                        streams[label] = stream
                        partitions.append((label, stream))
                    streams[label].append((sample, size))
            else:
                assert self.partition_size is not None
                current: StagedSamples[tuple[_PreparedSample, int]] | None = None
                current_size = 0
                for _, sample, size in self._staged_samples():
                    # A sample is never split. A sample larger than the target
                    # size simply becomes a one-sample partition.
                    if current is None or (current.count and current_size + size > self.partition_size):
                        current = StagedSamples(directory / f"{len(partitions)}.stage")
                        partitions.append((f"part{len(partitions) + 1:04d}", current))
                        current_size = 0
                    current.append((sample, size))
                    current_size += size
        except BaseException:
            for _, stream in partitions:
                stream.close()
            raise

        for _, stream in partitions:
            stream.close()
        return partitions

    def _samples_with_partition_metadata(self) -> Iterator[tuple[_PreparedSample, int]]:
        assert self.partition_by is not None
        derived = any(
            self.partition_by in descriptor["produces"]
            for descriptor in self.contract.derived.get(SAMPLE_LEVEL, {}).values()
        )
        if not derived:
            for _, sample, size in self._staged_samples():
                yield sample, size
            return

        # Derived fields normally appear while writing Parquet. A partition
        # key is needed earlier, so compute just enough metadata to group the
        # sample before each part is built.
        batch: list[tuple[_PreparedSample, int]] = []
        for _, sample, size in self._staged_samples():
            batch.append((sample, size))
            if len(batch) == self.batch_size:
                yield from self._apply_partition_metadata(batch)
                batch = []
        yield from self._apply_partition_metadata(batch)

    def _apply_partition_metadata(
        self, batch: list[tuple[_PreparedSample, int]]
    ) -> Iterator[tuple[_PreparedSample, int]]:
        rows = [dict(sample.metadata) for sample, _ in batch]
        self.contract.apply_derived(SAMPLE_LEVEL, rows)
        for (sample, size), metadata in zip(batch, rows, strict=True):
            yield sample.replace_metadata(metadata), size

    def _write_partitioned_archives(self) -> BuildResult:
        from ..tacocat import consolidate

        partitions = self._stage_partitions()
        if len(partitions) == 1:
            # Avoid producing a one-part TACOCAT. The requested output path is
            # clearer and has exactly the same contents.
            return self._write_single_archive()

        stem = self.output.stem
        parent = self.output.parent
        suffix = self.output.suffix or _ARCHIVE_SUFFIX
        outputs = [parent / f"{stem}_{label}{suffix}" for label, _ in partitions]
        for output in outputs:
            self._validate_output(output)
        tacocat_dir = parent / ".tacocat"
        if tacocat_dir.exists() and not self.overwrite:
            raise FileExistsError(f"{tacocat_dir} already exists (set overwrite=True)")
        if tacocat_dir.exists() and not tacocat_dir.is_dir():
            raise WriterError(f"TACOCAT output exists and is not a directory: {tacocat_dir}")

        parent.mkdir(parents=True, exist_ok=True)

        # Parts and the TACOCAT index stay hidden in this release directory
        # until every build succeeds. publish_many then exposes them together.
        with tempfile.TemporaryDirectory(prefix=f".{stem}.release-", dir=parent) as name:
            release = Path(name)
            jobs = list(zip(outputs, partitions, strict=True))
            if self.workers == 1:
                results = [
                    self._write_partition(release, output, label, stream, True) for output, (label, stream) in jobs
                ]
            else:
                results = []

                # Worker threads do not own progress bars; the main thread
                # reports each completed partition using its sample count.
                with (
                    self._show_progress(self.sample_count, f"building {self.output.name}") as progress,
                    ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as executor,
                ):
                    futures = [
                        executor.submit(self._write_partition, release, output, label, stream, False)
                        for output, (label, stream) in jobs
                    ]
                    for future in futures:
                        result = future.result()
                        results.append(result)
                        progress.update(result.samples)

            # The consolidated metadata is built from the finished parts, so
            # it cannot point at an archive that failed halfway through.
            tacocat = consolidate(
                [item.path for item in results],
                release,
                row_group_size=self.row_group_size,
                parquet_options=self.parquet_options,
            )
            replacements = [(result.path, output) for result, output in zip(results, outputs, strict=True)]
            replacements.append((tacocat, tacocat_dir))
            publish_many(replacements, overwrite=self.overwrite)
        return BuildResult(
            path=tacocat_dir,
            samples=sum(item.samples for item in results),
            data_files=sum(item.data_files for item in results),
            metadata_files=len(self.contract.levels),
            size=sum(item.size for item in results),
            parts=tuple(outputs),
        )

    def _write_partition(
        self,
        release: Path,
        output: Path,
        label: str,
        samples: StagedSamples[tuple[_PreparedSample, int]],
        show_progress: bool,
    ) -> BuildResult:
        def records() -> Iterator[tuple[int, _PreparedSample]]:
            for index, (sample, _) in enumerate(samples):
                yield index, sample

        logger.info("building partition %s with %d samples -> %s", label, samples.count, output)
        return self._write_archive(release / output.name, records, samples.count, show_progress=show_progress)

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
