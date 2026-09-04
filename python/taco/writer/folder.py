from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contract.collection import Collection
from ..contract.naming import COLLECTION_FILENAME, DATA_DIR, METADATA_DIR, level_to_filename
from ..errors import WriterError
from ._base import BuildResult, StagedWriter
from .levels import LevelTableWriter

if TYPE_CHECKING:
    from .._view import DatasetView

__all__ = ["FolderWriter", "open_folder"]

logger = logging.getLogger("taco")


def _looks_like_taco_folder(path: Path) -> bool:
    return (path / COLLECTION_FILENAME).is_file() and (path / METADATA_DIR).is_dir()


class FolderWriter(StagedWriter):
    """Write (or append to) a FOLDER-mode TACO dataset.

    ``append=True`` opens an existing folder built with the same contract and
    continues the sample numbering; the ``collection`` you pass becomes the
    new ``COLLECTION.json`` (bump ``dataset_version`` accordingly).
    ``link=True`` hard-links assets instead of copying when possible.
    """

    def __init__(
        self,
        collection: Collection,
        directory: str | os.PathLike[str],
        *,
        append: bool = False,
        overwrite: bool = False,
        link: bool = False,
        staging_dir: str | os.PathLike[str] | None = None,
        row_group_size: int = 65_536,
        batch_size: int = 10_000,
        parquet_options: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            collection,
            staging_dir=staging_dir,
            batch_size=batch_size,
            row_group_size=row_group_size,
            parquet_options=parquet_options,
        )
        self.directory = Path(directory).expanduser().resolve()
        if self.directory.suffix.lower() in {".zip", ".tacozip"}:
            raise WriterError("a FOLDER dataset is a directory, not an archive name")
        if append and overwrite:
            raise ValueError("append and overwrite are mutually exclusive")
        self.append = append
        self.overwrite = overwrite
        self.link = link

    def _existing_dataset(self) -> DatasetView | None:
        from .._view import open_view

        directory = self.directory
        if self.append:
            if not _looks_like_taco_folder(directory):
                raise WriterError(f"append=True needs an existing FOLDER dataset at {directory}")
            existing = open_view(directory)
            if existing.container != "folder":
                raise WriterError(f"{directory} is not a FOLDER dataset")
            if existing.contract != self.contract:
                raise WriterError("the existing dataset was built with a different contract")
            return existing
        if directory.exists():
            if not directory.is_dir():
                raise WriterError(f"FOLDER output exists and is not a directory: {directory}")
            entries = list(directory.iterdir())
            if entries:
                if not self.overwrite:
                    raise FileExistsError(f"directory is not empty (set overwrite=True): {directory}")
                if not _looks_like_taco_folder(directory):
                    raise WriterError(f"refusing to overwrite {directory}: it is not a TACO folder dataset")
        return None

    def _place(self, source: Path, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.link:
            try:
                if target.exists():
                    target.unlink()
                os.link(source, target)
                return source.stat().st_size
            except OSError:
                logger.debug("hard link failed for %s, copying instead", source)
        shutil.copyfile(source, target)
        return target.stat().st_size

    def _build(self) -> BuildResult:
        existing = self._existing_dataset()
        if existing is not None:
            return self._write_dataset(self.directory, existing)

        self.directory.parent.mkdir(parents=True, exist_ok=True)
        build_directory = Path(tempfile.mkdtemp(prefix=f".{self.directory.name}.build-", dir=self.directory.parent))
        try:
            result = self._write_dataset(build_directory, None)
            self._publish(build_directory)
            return result
        finally:
            shutil.rmtree(build_directory, ignore_errors=True)

    def _publish(self, source: Path) -> None:
        if not self.directory.exists():
            os.replace(source, self.directory)
            return
        if not self.overwrite and any(self.directory.iterdir()):
            raise FileExistsError(f"directory is not empty (set overwrite=True): {self.directory}")

        backup = Path(tempfile.mkdtemp(prefix=f".{self.directory.name}.backup-", dir=self.directory.parent))
        backup.rmdir()
        os.replace(self.directory, backup)
        try:
            os.replace(source, self.directory)
        except BaseException:
            os.replace(backup, self.directory)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    def _write_dataset(self, directory: Path, existing: DatasetView | None) -> BuildResult:
        start = existing.sample_count if existing is not None else 0
        data_dir = directory / DATA_DIR
        data_dir.mkdir(exist_ok=True)
        created: list[Path] = []
        copied_files = 0
        copied_bytes = 0
        temp_metadata = Path(tempfile.mkdtemp(prefix=".taco-", dir=directory))
        try:
            tables = LevelTableWriter(
                self.contract,
                temp_metadata,
                with_offsets=False,
                parquet_options=self.parquet_options,
                row_group_size=self.row_group_size,
                batch_size=self.batch_size,
            )
            try:
                if existing is not None:
                    for level in self.contract.levels:
                        tables.write_existing(level, existing.level(level))
                for local_index, sample, _ in self._records():
                    index = start + local_index
                    sample_dir = data_dir / str(index)
                    if sample_dir.exists():
                        raise WriterError(f"{sample_dir} already exists")
                    created.append(sample_dir)
                    sample_dir.mkdir()
                    for asset in sample.assets:
                        assert isinstance(asset.source, Path)
                        target = sample_dir if asset.path is None else sample_dir / asset.path
                        if asset.path is None:
                            sample_dir.rmdir()
                        copied_bytes += self._place(asset.source, target)
                        copied_files += 1
                    tables.add_sample(index, sample)
                paths = tables.close()
            except BaseException:
                tables.abort()
                raise

            metadata_dir = directory / METADATA_DIR
            metadata_dir.mkdir(exist_ok=True)
            temp_collection = temp_metadata / COLLECTION_FILENAME
            temp_collection.write_text(self.collection.to_json(), encoding="utf-8")
            replacements = [
                (paths[level], metadata_dir / level_to_filename(level)) for level in self.contract.levels
            ]
            replacements.append((temp_collection, directory / COLLECTION_FILENAME))
            self._replace_metadata(replacements, temp_metadata / "previous")
        except BaseException:
            for path in created:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(temp_metadata, ignore_errors=True)

        return BuildResult(
            path=self.directory,
            samples=start + self._journal.count,
            data_files=copied_files,
            metadata_files=len(self.contract.levels),
            size=copied_bytes,
        )

    @staticmethod
    def _replace_metadata(replacements: list[tuple[Path, Path]], backup: Path) -> None:
        backup.mkdir()
        previous: list[tuple[Path, Path]] = []
        installed: list[Path] = []
        try:
            for _, target in replacements:
                if target.exists():
                    saved = backup / target.name
                    os.replace(target, saved)
                    previous.append((saved, target))
            for source, target in replacements:
                os.replace(source, target)
                installed.append(target)
        except BaseException:
            for target in installed:
                target.unlink(missing_ok=True)
            for saved, target in previous:
                os.replace(saved, target)
            raise


def open_folder(collection: Collection, directory: str | os.PathLike[str], **options: Any) -> FolderWriter:
    """Open a FOLDER-mode writer (appendable, spec section 7.4)."""
    return FolderWriter(collection, directory, **options)
