from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
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
from ._base import _BuildResult, _Writer
from .journal import Journal
from .levels import LevelTableWriter

logger = logging.getLogger("taco")

# cozip spec 14.5: a TACO-profile archive is a .zip. The authoritative
# signal is the profile byte in the index, never the file name.
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


class _ArchiveWriter(_Writer):
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
    ) -> None:
        if not isinstance(collection, Collection):
            raise TypeError("collection must be a Collection")
        normalized_output = _normalize_output(output)
        if partition_size is not None and partition_by is not None:
            raise ValueError("use either partition_size or partition_by, not both")
        parsed_partition_size = None if partition_size is None else parse_size(partition_size)
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
        )
        self.output = normalized_output
        self.overwrite = overwrite
        self.partition_size = parsed_partition_size
        self.partition_by = partition_by

    def _build(self) -> _BuildResult:
        if self.partition_size is None and self.partition_by is None:
            return self._run_single()
        return self._run_partitioned()

    def _check_destination(self, output: Path) -> None:
        if output.exists():
            if not self.overwrite:
                raise FileExistsError(f"output already exists (set overwrite=True): {output}")
            if not output.is_file():
                raise WriterError(f"output exists and is not a file: {output}")

    def _run_single(self) -> _BuildResult:
        self._check_destination(self.output)
        return self._build_archive(self.output, lambda: ((index, sample) for index, sample, _ in self._records()))

    def _partitions(self) -> list[tuple[str, Journal[tuple[_PreparedSample, int]]]]:
        directory = self._stage / "partitions"
        directory.mkdir()
        partitions: list[tuple[str, Journal[tuple[_PreparedSample, int]]]] = []
        labels: dict[str, Any] = {}

        try:
            if self.partition_by is not None:
                journals: dict[str, Journal[tuple[_PreparedSample, int]]] = {}
                for sample, size in self._partition_records():
                    value = sample.metadata[self.partition_by]
                    label = sanitize_filename(str(value))
                    if label in labels and labels[label] != value:
                        raise WriterError(
                            f"partition values {labels[label]!r} and {value!r} collide on file name {label!r}"
                        )
                    labels[label] = value
                    if label not in journals:
                        journal: Journal[tuple[_PreparedSample, int]] = Journal(directory / f"{len(journals)}.journal")
                        journals[label] = journal
                        partitions.append((label, journal))
                    journals[label].append((sample, size))
            else:
                assert self.partition_size is not None
                current: Journal[tuple[_PreparedSample, int]] | None = None
                current_size = 0
                for _, sample, size in self._records():
                    if current is None or (current.count and current_size + size > self.partition_size):
                        current = Journal(directory / f"{len(partitions)}.journal")
                        partitions.append((f"part{len(partitions) + 1:04d}", current))
                        current_size = 0
                    current.append((sample, size))
                    current_size += size
        except BaseException:
            for _, journal in partitions:
                journal.close()
            raise

        for _, journal in partitions:
            journal.close()
        return partitions

    def _partition_records(self) -> Iterator[tuple[_PreparedSample, int]]:
        assert self.partition_by is not None
        derived = any(
            self.partition_by in descriptor["produces"]
            for descriptor in self.contract.derived.get(SAMPLE_LEVEL, {}).values()
        )
        if not derived:
            for _, sample, size in self._records():
                yield sample, size
            return

        batch: list[tuple[_PreparedSample, int]] = []
        for _, sample, size in self._records():
            batch.append((sample, size))
            if len(batch) == self.batch_size:
                yield from self._derive_partition_batch(batch)
                batch = []
        yield from self._derive_partition_batch(batch)

    def _derive_partition_batch(
        self, batch: list[tuple[_PreparedSample, int]]
    ) -> Iterator[tuple[_PreparedSample, int]]:
        rows = [dict(sample.metadata) for sample, _ in batch]
        self.contract.apply_derived(SAMPLE_LEVEL, rows)
        for (sample, size), metadata in zip(batch, rows, strict=True):
            yield sample.replace_metadata(metadata), size

    def _run_partitioned(self) -> _BuildResult:
        from ..tacocat import consolidate

        partitions = self._partitions()
        if len(partitions) == 1:
            return self._run_single()

        stem = self.output.stem
        parent = self.output.parent
        suffix = self.output.suffix or _ARCHIVE_SUFFIX
        outputs = [parent / f"{stem}_{label}{suffix}" for label, _ in partitions]
        for output in outputs:
            self._check_destination(output)
        tacocat_dir = parent / ".tacocat"
        if tacocat_dir.exists() and not self.overwrite:
            raise FileExistsError(f"{tacocat_dir} already exists (set overwrite=True)")
        if tacocat_dir.exists() and not tacocat_dir.is_dir():
            raise WriterError(f"TACOCAT output exists and is not a directory: {tacocat_dir}")

        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{stem}.release-", dir=parent) as name:
            release = Path(name)
            results: list[_BuildResult] = []
            for output, (label, journal) in zip(outputs, partitions, strict=True):

                def samples(
                    journal: Journal[tuple[_PreparedSample, int]] = journal,
                ) -> Iterator[tuple[int, _PreparedSample]]:
                    for index, (sample, _) in enumerate(journal):
                        yield index, sample

                logger.info("building partition %s with %d samples -> %s", label, journal.count, output)
                results.append(self._build_archive(release / output.name, samples))

            tacocat = consolidate(
                [item.path for item in results],
                release,
                row_group_size=self.row_group_size,
                parquet_options=self.parquet_options,
            )
            replacements = [(result.path, output) for result, output in zip(results, outputs, strict=True)]
            replacements.append((tacocat, tacocat_dir))
            publish_many(replacements, overwrite=self.overwrite)
        return _BuildResult(
            path=tacocat_dir,
            samples=sum(item.samples for item in results),
            data_files=sum(item.data_files for item in results),
            metadata_files=len(self.contract.levels),
            size=sum(item.size for item in results),
            parts=tuple(outputs),
        )

    def _build_archive(
        self,
        output: Path,
        samples: Callable[[], Iterator[tuple[int, _PreparedSample]]],
    ) -> _BuildResult:
        temporary_output: Path | None = None
        with tempfile.TemporaryDirectory(prefix="build-", dir=self._stage) as name:
            stage = Path(name)
            files: list[tuple[str, Path]] = []
            sample_count = 0
            for index, sample in samples():
                files.extend(_data_entries(index, sample))
                sample_count += 1
            names = _priority_names(self.collection)
            layout = cozip_plan(files, names)
            offsets = layout.offsets

            tables = LevelTableWriter(
                self.contract,
                stage / METADATA_DIR,
                with_offsets=True,
                parquet_options=self.parquet_options,
                row_group_size=self.row_group_size,
                batch_size=self.batch_size,
            )
            try:
                for index, sample in samples():
                    tables.add_sample(index, sample, offsets.__getitem__)
                paths = tables.close()
            except BaseException:
                tables.abort()
                raise

            collection_path = stage / COLLECTION_FILENAME
            collection_path.write_text(self._collection_json(tables.summaries), encoding="utf-8")
            priority_files = [(COLLECTION_FILENAME, collection_path)]
            priority_files += [
                (f"{METADATA_DIR}/{level_to_filename(level)}", paths[level]) for level in self.contract.levels
            ]

            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
            os.close(descriptor)
            temporary_output = Path(temporary_name)
            try:
                cozip_write(temporary_output, layout, priority_files)
                # mkstemp creates 0600; a published archive follows the umask.
                umask = os.umask(0)
                os.umask(umask)
                temporary_output.chmod(0o666 & ~umask)
                publish_file(temporary_output, output, overwrite=self.overwrite)
                temporary_output = None
                return _BuildResult(
                    path=output,
                    samples=sample_count,
                    data_files=len(files),
                    metadata_files=len(self.contract.levels),
                    size=output.stat().st_size,
                )
            finally:
                if temporary_output is not None:
                    temporary_output.unlink(missing_ok=True)


def _open_archive(
    collection: Collection,
    output: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    row_group_size: int = 65_536,
    batch_size: int = 10_000,
    parquet_options: Mapping[str, Any] | None = None,
    partition_size: int | str | None = None,
    partition_by: str | None = None,
) -> _ArchiveWriter:
    return _ArchiveWriter(
        collection,
        output,
        overwrite=overwrite,
        row_group_size=row_group_size,
        batch_size=batch_size,
        parquet_options=parquet_options,
        partition_size=partition_size,
        partition_by=partition_by,
    )
