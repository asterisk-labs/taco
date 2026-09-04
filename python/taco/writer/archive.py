from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from ..contract.collection import Collection
from ..contract.contract import COLLECTION_LEVEL
from ..contract.naming import (
    COLLECTION_FILENAME,
    DATA_DIR,
    METADATA_DIR,
    level_to_filename,
    parse_size,
    sanitize_filename,
)
from ..contract.sample import Sample
from ..cozip import cozip_plan, cozip_write
from ..errors import WriterError
from ._base import BuildResult, StagedWriter
from .journal import Journal
from .levels import LevelTableWriter

__all__ = ["ARCHIVE_SUFFIX", "TacoWriter", "open_writer"]

logger = logging.getLogger("taco")

# cozip spec 14.5: a TACO-profile archive is a .zip. The authoritative
# signal is the profile byte in the index, never the file name.
ARCHIVE_SUFFIX = ".zip"


def _normalize_output(output: str | os.PathLike[str]) -> Path:
    path = Path(output).expanduser()
    if path.suffix == "":
        path = path.with_name(path.name + ARCHIVE_SUFFIX)
    elif path.suffix != ARCHIVE_SUFFIX:
        raise ValueError("TACO archive output must end in .zip")
    return path.resolve()


def _publish_archive(source: Path, output: Path, *, overwrite: bool) -> None:
    """Publish a completed archive without reopening the destination race."""
    if overwrite:
        os.replace(source, output)
        return

    try:
        if os.name == "nt":
            # Windows rename fails when the destination already exists.
            os.rename(source, output)
        else:
            # POSIX rename replaces its destination. A hard link gives us the
            # no-replace operation Python's portable API does not expose.
            os.link(source, output)
            source.unlink()
    except FileExistsError as exc:
        raise FileExistsError(f"output already exists (set overwrite=True): {output}") from exc


def _priority_names(collection: Collection) -> list[str]:
    return [
        COLLECTION_FILENAME,
        *(f"{METADATA_DIR}/{level_to_filename(level)}" for level in collection.contract.levels),
    ]


def _data_entries(sample_index: int, sample: Sample) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for asset in sample.assets:
        if not isinstance(asset.source, Path):
            raise TypeError("inline assets must be materialized before planning")
        name = f"{DATA_DIR}/{sample_index}" if asset.path is None else f"{DATA_DIR}/{sample_index}/{asset.path}"
        entries.append((name, asset.source))
    return entries


class TacoWriter(StagedWriter):
    """Collect samples cheaply, then materialize one archive with ``run()``.

    ``partition_size`` (for example ``"4GB"``) or ``partition_by`` (a
    collection-level field) split the samples across several archives named
    ``<stem>_part0001.zip`` / ``<stem>_<value>.zip`` and consolidate
    their metadata into a ``.tacocat`` directory next to them.
    """

    def __init__(
        self,
        collection: Collection,
        output: str | os.PathLike[str],
        *,
        staging_dir: str | os.PathLike[str] | None = None,
        overwrite: bool = False,
        row_group_size: int = 65_536,
        batch_size: int = 10_000,
        parquet_options: Mapping[str, Any] | None = None,
        partition_size: int | str | None = None,
        partition_by: str | None = None,
    ) -> None:
        super().__init__(
            collection,
            staging_dir=staging_dir,
            batch_size=batch_size,
            row_group_size=row_group_size,
            parquet_options=parquet_options,
        )
        self.output = _normalize_output(output)
        self.overwrite = overwrite
        self.partition_size = None if partition_size is None else parse_size(partition_size)
        self.partition_by = partition_by
        if partition_size is not None and partition_by is not None:
            raise ValueError("use either partition_size or partition_by, not both")
        if partition_by is not None and partition_by not in self.contract.metadata[COLLECTION_LEVEL]:
            raise ValueError(
                f"partition_by field {partition_by!r} is not a collection-level field; "
                f"available: {list(self.contract.metadata[COLLECTION_LEVEL])}"
            )

    def _build(self) -> BuildResult:
        if self.partition_size is None and self.partition_by is None:
            return self._run_single()
        return self._run_partitioned()

    def _check_destination(self, output: Path) -> None:
        if output.exists():
            if not self.overwrite:
                raise FileExistsError(f"output already exists (set overwrite=True): {output}")
            if not output.is_file():
                raise WriterError(f"output exists and is not a file: {output}")

    def _run_single(self) -> BuildResult:
        self._check_destination(self.output)
        return self._build_archive(self.output, lambda: ((index, sample) for index, sample, _ in self._records()))

    def _partitions(self) -> list[tuple[str, Journal]]:
        directory = self._stage / "partitions"
        directory.mkdir()
        partitions: list[tuple[str, Journal]] = []
        labels: dict[str, Any] = {}

        try:
            if self.partition_by is not None:
                journals: dict[str, Journal] = {}
                for _, sample, size in self._records():
                    value = sample.metadata[COLLECTION_LEVEL][self.partition_by]
                    label = sanitize_filename(str(value))
                    if label in labels and labels[label] != value:
                        raise WriterError(
                            f"partition values {labels[label]!r} and {value!r} collide on file name {label!r}"
                        )
                    labels[label] = value
                    if label not in journals:
                        journal = Journal(directory / f"{len(journals)}.journal")
                        journals[label] = journal
                        partitions.append((label, journal))
                    journals[label].append((sample, size))
            else:
                assert self.partition_size is not None
                current: Journal | None = None
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

    def _run_partitioned(self) -> BuildResult:
        from ..tacocat import consolidate

        partitions = self._partitions()
        if len(partitions) == 1:
            return self._run_single()

        stem = self.output.stem
        parent = self.output.parent
        suffix = self.output.suffix or ARCHIVE_SUFFIX
        outputs = [parent / f"{stem}_{label}{suffix}" for label, _ in partitions]
        for output in outputs:
            self._check_destination(output)
        tacocat_dir = parent / ".tacocat"
        if tacocat_dir.exists() and not self.overwrite:
            raise FileExistsError(f"{tacocat_dir} already exists (set overwrite=True)")

        results: list[BuildResult] = []
        for output, (label, journal) in zip(outputs, partitions, strict=True):

            def samples(journal: Journal = journal) -> Iterator[tuple[int, Sample]]:
                for index, (sample, _) in enumerate(journal):
                    yield index, sample

            logger.info("building partition %s with %d samples -> %s", label, journal.count, output)
            results.append(self._build_archive(output, samples))

        tacocat = consolidate(
            [item.path for item in results],
            parent,
            overwrite=self.overwrite,
            row_group_size=self.row_group_size,
            parquet_options=self.parquet_options,
        )
        return BuildResult(
            path=tacocat,
            samples=sum(item.samples for item in results),
            data_files=sum(item.data_files for item in results),
            metadata_files=len(self.contract.levels),
            size=sum(item.size for item in results),
            parts=tuple(item.path for item in results),
        )

    def _build_archive(
        self,
        output: Path,
        samples: Callable[[], Iterator[tuple[int, Sample]]],
    ) -> BuildResult:
        stage = Path(tempfile.mkdtemp(prefix="build-", dir=self._stage))
        temporary_output: Path | None = None
        try:
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
            collection_path.write_text(self.collection.to_json(), encoding="utf-8")
            priority_files = [(COLLECTION_FILENAME, collection_path)]
            priority_files += [
                (f"{METADATA_DIR}/{level_to_filename(level)}", paths[level]) for level in self.contract.levels
            ]

            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
            os.close(descriptor)
            temporary_output = Path(temporary_name)
            cozip_write(temporary_output, layout, priority_files)
            # mkstemp creates 0600; a published archive follows the umask.
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(temporary_output, 0o666 & ~umask)
            _publish_archive(temporary_output, output, overwrite=self.overwrite)
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
            shutil.rmtree(stage, ignore_errors=True)


def open_writer(collection: Collection, output: str | os.PathLike[str], **options: Any) -> TacoWriter:
    """Open a staged writer that publishes one immutable cozip profile-2 archive."""
    return TacoWriter(collection, output, **options)
