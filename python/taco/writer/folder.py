from __future__ import annotations

import contextlib
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..container.publish import exchange, publish_file, publish_many
from ..contract.collection import Collection
from ..contract.contract import SAMPLE_ID, SAMPLE_LEVEL
from ..contract.naming import COLLECTION_FILENAME, DATA_DIR, METADATA_DIR, level_to_filename
from ..errors import WriterError
from .base import BuildResult, Writer
from .metadata import MetadataTableWriter

if TYPE_CHECKING:
    from ..container.view import DatasetView

logger = logging.getLogger("taco")

# Stage on the dataset's file system for same-volume renames. Readers ignore it.
APPEND_DIR = ".taco-append"
_LOCK = "lock"
# Backup of METADATA/ while two renames switch it.
_PREVIOUS = "previous"


def _looks_like_taco_folder(path: Path) -> bool:
    return (path / COLLECTION_FILENAME).is_file() and (path / DATA_DIR).is_dir() and (path / METADATA_DIR).is_dir()


def _is_sample_directory(name: str) -> bool:
    return name.isascii() and name.isdigit() and str(int(name)) == name


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _sync(path: Path) -> None:
    """Flush a file or directory before renaming it."""
    directory = path.is_dir()
    if directory and sys.platform == "win32":
        return  # Windows cannot open a directory
    try:
        descriptor = os.open(path, os.O_RDWR if sys.platform == "win32" else os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Some file systems cannot flush a directory.
        if not directory:
            raise


def _parquet_rows(path: Path) -> int:
    return int(pq.read_metadata(path).num_rows)  # type: ignore[no-untyped-call]


def _try_lock(descriptor: int) -> bool:
    """Try a process-owned, nonblocking file lock."""
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        return False
    return True


def _same_file(descriptor: int, path: Path) -> bool:
    try:
        return os.path.samestat(os.fstat(descriptor), path.stat())
    except FileNotFoundError:
        return False


class _Append:
    """Stage an append under a process lock and recover interrupted writes."""

    def __init__(self, dataset: Path) -> None:
        self.dataset = dataset
        self.path = dataset / APPEND_DIR
        self._descriptor: int | None = None
        # Keep recovery files when rollback fails.
        self._keep = False

    def __enter__(self) -> _Append:
        self._descriptor = self._lock()
        try:
            self._recover()
        except BaseException:
            self._keep = True
            self._release()
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._release()

    def _lock(self) -> int:
        lock = self.path / _LOCK
        for _ in range(10):
            self.path.mkdir(exist_ok=True)
            try:
                descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o666)
            except FileNotFoundError:  # a writer that just finished removed the folder
                continue
            try:
                locked = _try_lock(descriptor)
            except OSError as exc:
                # Some file systems, including Lustre, may not support flock.
                logger.warning("cannot lock %s (%s); nothing stops another append meanwhile", lock, exc)
                return descriptor
            if not locked:
                os.close(descriptor)
                raise WriterError(f"another writer is appending to {self.dataset}")
            # Retry if the previous writer removed this lock file after we opened it.
            if _same_file(descriptor, lock):
                return descriptor
            os.close(descriptor)
        raise WriterError(f"could not lock {lock}")

    def _recover(self) -> None:
        """Recover an interrupted append from the state of its metadata switch."""
        metadata = self.dataset / METADATA_DIR
        previous = self.path / _PREVIOUS
        collection = self.path / COLLECTION_FILENAME
        if previous.is_dir():
            if not metadata.exists():
                # The switch stopped between renames; restore old metadata.
                previous.rename(metadata)
            elif not (self.path / METADATA_DIR).exists() and collection.is_file():
                # New metadata is committed; publish its collection.
                publish_file(collection, self.dataset / COLLECTION_FILENAME, overwrite=True)
            _sync(self.dataset)
        elif self._exchanged():
            # An exchange committed new metadata; publish its collection.
            if collection.is_file():
                publish_file(collection, self.dataset / COLLECTION_FILENAME, overwrite=True)
            _sync(self.dataset)
        for entry in self.path.iterdir():
            if entry.name != _LOCK:
                _remove(entry)

        # Remove samples moved before a switch that never committed.
        samples = metadata / level_to_filename(SAMPLE_LEVEL)
        data = self.dataset / DATA_DIR
        if samples.is_file() and data.is_dir():
            published = _parquet_rows(samples)
            with os.scandir(data) as entries:
                stale = [
                    Path(entry.path)
                    for entry in entries
                    if _is_sample_directory(entry.name) and int(entry.name) >= published
                ]
            for path in stale:
                _remove(path)

    def commit(self, indexes: range) -> None:
        """Publish staged samples; installing METADATA/ commits the append."""
        data = self.dataset / DATA_DIR
        metadata = self.dataset / METADATA_DIR
        staged = self.path / METADATA_DIR
        previous = self.path / _PREVIOUS
        # Flush staged files before the metadata switch.
        for path in (*staged.iterdir(), self.path / COLLECTION_FILENAME):
            _sync(path)
        moved: list[Path] = []
        try:
            for index in indexes:
                target = data / str(index)
                (self.path / DATA_DIR / str(index)).rename(target)
                moved.append(target)
            _sync(data)
            # An exchange keeps METADATA/ in place; two renames leave a gap.
            if not exchange(staged, metadata):
                metadata.rename(previous)
                staged.rename(metadata)
            _sync(self.dataset)
            publish_file(self.path / COLLECTION_FILENAME, self.dataset / COLLECTION_FILENAME, overwrite=True)
        except BaseException:
            self._undo(moved)
            raise
        _sync(self.dataset)

    def _undo(self, moved: list[Path]) -> None:
        metadata = self.dataset / METADATA_DIR
        staged = self.path / METADATA_DIR
        previous = self.path / _PREVIOUS
        if not (self.path / COLLECTION_FILENAME).exists():
            return  # interrupted after the collection was published: nothing to undo
        try:
            if previous.is_dir():
                if metadata.exists():
                    metadata.rename(staged)
                previous.rename(metadata)
            elif self._exchanged() and not exchange(staged, metadata):
                raise WriterError(f"could not switch {metadata} back")
        except BaseException:
            self._keep = True
            raise
        for path in moved:
            shutil.rmtree(path, ignore_errors=True)

    def _exchanged(self) -> bool:
        """Whether an exchange installed the staged metadata."""
        # Both directories exist before and after an exchange. An append adds
        # samples, so the new metadata is the one with more sample rows.
        name = level_to_filename(SAMPLE_LEVEL)
        try:
            staged = _parquet_rows(self.path / METADATA_DIR / name)
            published = _parquet_rows(self.dataset / METADATA_DIR / name)
        except (OSError, pa.ArrowException):
            return False  # nothing staged, or staging never finished
        return published > staged

    def _release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        if self._keep:
            os.close(descriptor)
            return
        for name in (DATA_DIR, METADATA_DIR, COLLECTION_FILENAME, _PREVIOUS):
            with contextlib.suppress(OSError):
                _remove(self.path / name)
        # Windows requires closing the lock file before removing it.
        if sys.platform == "win32":
            os.close(descriptor)
        with contextlib.suppress(OSError):
            (self.path / _LOCK).unlink()
            self.path.rmdir()
        if sys.platform != "win32":
            os.close(descriptor)


class FolderWriter(Writer):
    def __init__(
        self,
        collection: Collection,
        directory: str | PathLike[str],
        *,
        append: bool = False,
        overwrite: bool = False,
        link: bool = False,
        row_group_size: int = 65_536,
        batch_size: int = 10_000,
        parquet_options: Mapping[str, Any] | None = None,
        progress: bool = False,
    ) -> None:
        output = Path(directory).expanduser().resolve()
        if output.suffix.lower() == ".zip":
            raise WriterError("a FOLDER dataset is a directory, not an archive name")
        if append and overwrite:
            raise ValueError("append and overwrite are mutually exclusive")
        super().__init__(
            collection,
            batch_size=batch_size,
            row_group_size=row_group_size,
            parquet_options=parquet_options,
            progress=progress,
        )
        self.directory = output
        self.append = append
        self.overwrite = overwrite
        self.link = link

    def _dataset_to_append(self) -> DatasetView:
        from ..container.view import open_view

        directory = self.directory
        if not _looks_like_taco_folder(directory):
            raise WriterError(f"append=True needs an existing FOLDER dataset at {directory}")
        existing = open_view(directory)
        if existing.container != "folder":
            raise WriterError(f"{directory} is not a FOLDER dataset")

        # Appending cannot change the identity or layout of existing samples.
        if existing.collection.id != self.collection.id:
            raise WriterError("append cannot change the dataset id")
        if existing.contract != self.contract:
            raise WriterError("the existing dataset was built with a different contract")
        existing_collection = existing.collection.to_dict()
        incompatible = [
            name for name, value in self.contract.extension_metadata().items() if existing_collection.get(name) != value
        ]
        if incompatible:
            raise WriterError(f"append uses different extension metadata: {incompatible}")
        sample_table = existing.level(SAMPLE_LEVEL)
        if SAMPLE_ID not in sample_table.column_names:
            raise WriterError("the existing dataset has no sample id column and cannot be appended")
        repeated: list[str] = []
        for batch in sample_table.select([SAMPLE_ID]).to_batches():
            repeated.extend(self._sample_ids.duplicates(batch.column(0).to_pylist(), limit=3 - len(repeated)))
            if len(repeated) == 3:
                break
        if repeated:
            raise WriterError(f"append would duplicate sample ids: {sorted(repeated)}")
        return existing

    def _check_destination(self) -> None:
        directory = self.directory
        if directory.exists():
            if not directory.is_dir():
                raise WriterError(f"FOLDER output exists and is not a directory: {directory}")
            entries = list(directory.iterdir())
            if entries:
                if not self.overwrite:
                    raise FileExistsError(f"directory is not empty (set overwrite=True): {directory}")

                # overwrite=True should never turn this writer into a generic
                # recursive directory deletion tool.
                if not _looks_like_taco_folder(directory):
                    raise WriterError(f"refusing to overwrite {directory}: it is not a TACO folder dataset")

    def _copy_asset(self, source: Path, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.link:
            target.hardlink_to(source)
            return source.stat().st_size
        shutil.copyfile(source, target)
        if self.append:
            # Flush copied assets before publishing their directory.
            _sync(target)
        return target.stat().st_size

    def _build(self) -> BuildResult:
        if self.append:
            return self._append()
        self._check_destination()
        self.directory.parent.mkdir(parents=True, exist_ok=True)

        # A new dataset is built beside its destination and moved into place
        # only when every data and metadata file is ready. mkdtemp makes a
        # private directory; the dataset inside it follows the umask.
        build_root = Path(tempfile.mkdtemp(prefix=f".{self.directory.name}.build-", dir=self.directory.parent))
        try:
            build_directory = build_root / self.directory.name
            build_directory.mkdir()
            result = self._write_folder(build_directory, None)
            self._publish_new_folder(build_directory)
            return result
        finally:
            shutil.rmtree(build_root, ignore_errors=True)

    def _append(self) -> BuildResult:
        # Recovery may be needed even when METADATA/ is temporarily absent.
        if not _looks_like_taco_folder(self.directory) and not (self.directory / APPEND_DIR).is_dir():
            raise WriterError(f"append=True needs an existing FOLDER dataset at {self.directory}")
        # Finish staging before touching published paths.
        with _Append(self.directory) as transaction:
            existing = self._dataset_to_append()
            result = self._write_folder(transaction.path, existing)
            transaction.commit(range(existing.sample_count, result.samples))
        return result

    def _publish_new_folder(self, source: Path) -> None:
        if self.directory.exists() and not self.overwrite and any(self.directory.iterdir()):
            raise FileExistsError(f"directory is not empty (set overwrite=True): {self.directory}")
        publish_many([(source, self.directory)], overwrite=self.directory.exists())

    def _write_folder(self, directory: Path, existing: DatasetView | None) -> BuildResult:
        """Stage samples, metadata, and collection below directory."""
        # Appends continue the numeric ids already present in the dataset.
        start = existing.sample_count if existing is not None else 0
        data_dir = directory / DATA_DIR
        data_dir.mkdir(exist_ok=True)
        copied_files = 0
        copied_bytes = 0
        tables = MetadataTableWriter(
            self.contract,
            directory / METADATA_DIR,
            with_offsets=False,
            parquet_options=self.parquet_options,
            row_group_size=self.row_group_size,
            batch_size=self.batch_size,
        )
        try:
            if existing is not None:
                # Parquet files are rewritten rather than edited in place,
                # so existing rows must be copied before the new ones.
                for level in self.contract.levels:
                    tables.write_existing(level, existing.level(level))
            with self._show_progress(self.sample_count, f"writing {self.directory.name}") as progress:
                for local_index, sample, _ in self._staged_samples():
                    index = start + local_index
                    sample_dir = data_dir / str(index)
                    sample_dir.mkdir()
                    for asset in sample.assets:
                        assert isinstance(asset.source, Path)
                        assert asset.path is not None
                        copied_bytes += self._copy_asset(asset.source, sample_dir / asset.path)
                        copied_files += 1
                    tables.add_sample(index, sample)
                    progress.update()
            tables.close()
        except BaseException:
            tables.abort()
            raise
        (directory / COLLECTION_FILENAME).write_text(self._render_collection(tables.summaries), encoding="utf-8")

        return BuildResult(
            path=self.directory,
            samples=start + self.sample_count,
            data_files=copied_files,
            metadata_files=len(self.contract.levels),
            size=copied_bytes,
        )


__all__ = ["FolderWriter"]
