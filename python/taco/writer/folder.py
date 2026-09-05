from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._publish import publish_many
from ..contract.collection import Collection
from ..contract.naming import COLLECTION_FILENAME, DATA_DIR, METADATA_DIR, level_to_filename
from ..errors import WriterError
from ._base import BuildResult, StagedWriter
from .levels import LevelTableWriter

if TYPE_CHECKING:
    from .._view import DatasetView

__all__ = ["FolderWriter", "open_folder"]


def _looks_like_taco_folder(path: Path) -> bool:
    return (path / COLLECTION_FILENAME).is_file() and (path / METADATA_DIR).is_dir()


class FolderWriter(StagedWriter):
    """Write (or append to) a FOLDER-mode TACO dataset.

    ``append=True`` opens an existing folder built with the same contract and
    continues the sample numbering; the ``collection`` you pass becomes the
    new ``COLLECTION.json`` (bump ``dataset_version`` accordingly).
    ``link=True`` hard-links assets instead of copying them.
    """

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
    ) -> None:
        output = Path(directory).expanduser().resolve()
        if output.suffix.lower() in {".zip", ".tacozip"}:
            raise WriterError("a FOLDER dataset is a directory, not an archive name")
        if append and overwrite:
            raise ValueError("append and overwrite are mutually exclusive")
        super().__init__(
            collection,
            batch_size=batch_size,
            row_group_size=row_group_size,
            parquet_options=parquet_options,
        )
        self.directory = output
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
            target.hardlink_to(source)
            return source.stat().st_size
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
        if self.directory.exists() and not self.overwrite and any(self.directory.iterdir()):
            raise FileExistsError(f"directory is not empty (set overwrite=True): {self.directory}")
        publish_many([(source, self.directory)], overwrite=self.directory.exists())

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
            replacements = [(paths[level], metadata_dir / level_to_filename(level)) for level in self.contract.levels]
            replacements.append((temp_collection, directory / COLLECTION_FILENAME))
            publish_many(replacements, overwrite=True)
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


def open_folder(
    collection: Collection,
    directory: str | PathLike[str],
    *,
    append: bool = False,
    overwrite: bool = False,
    link: bool = False,
    row_group_size: int = 65_536,
    batch_size: int = 10_000,
    parquet_options: Mapping[str, Any] | None = None,
) -> FolderWriter:
    """Open a FOLDER-mode writer (appendable, spec section 7.4)."""
    return FolderWriter(
        collection,
        directory,
        append=append,
        overwrite=overwrite,
        link=link,
        row_group_size=row_group_size,
        batch_size=batch_size,
        parquet_options=parquet_options,
    )
