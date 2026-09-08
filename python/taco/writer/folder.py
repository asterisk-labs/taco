from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._publish import publish_many
from ..contract.collection import Collection
from ..contract.naming import COLLECTION_FILENAME, DATA_DIR, METADATA_DIR
from ..errors import WriterError
from .core import BuildResult, Writer
from .metadata_tables import MetadataTableWriter

if TYPE_CHECKING:
    from .._view import DatasetView


def _looks_like_taco_folder(path: Path) -> bool:
    return (path / COLLECTION_FILENAME).is_file() and (path / DATA_DIR).is_dir() and (path / METADATA_DIR).is_dir()


def _version_core(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split("+", 1)[0].split("-", 1)[0].split(".")
    return int(major), int(minor), int(patch)


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

    def _dataset_to_append(self) -> DatasetView | None:
        from .._view import open_view

        directory = self.directory
        if self.append:
            if not _looks_like_taco_folder(directory):
                raise WriterError(f"append=True needs an existing FOLDER dataset at {directory}")
            existing = open_view(directory)
            if existing.container != "folder":
                raise WriterError(f"{directory} is not a FOLDER dataset")

            # Existing sample ids and paths remain valid only when identity and
            # structure stay fixed. A higher minor version marks the append.
            if existing.collection.id != self.collection.id:
                raise WriterError("append cannot change the dataset id")
            if existing.contract != self.contract:
                raise WriterError("the existing dataset was built with a different contract")
            old_major, old_minor, _ = _version_core(existing.collection.dataset_version)
            new_major, new_minor, _ = _version_core(self.collection.dataset_version)
            if new_major != old_major or new_minor <= old_minor:
                raise WriterError("append needs a higher minor dataset version with the same major version")
            return existing
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
        return None

    def _copy_asset(self, source: Path, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.link:
            target.hardlink_to(source)
            return source.stat().st_size
        shutil.copyfile(source, target)
        return target.stat().st_size

    def _build(self) -> BuildResult:
        existing = self._dataset_to_append()
        if existing is not None:
            return self._write_folder(self.directory, existing)

        self.directory.parent.mkdir(parents=True, exist_ok=True)

        # A new dataset is built beside its destination and moved into place
        # only when every data and metadata file is ready.
        build_directory = Path(tempfile.mkdtemp(prefix=f".{self.directory.name}.build-", dir=self.directory.parent))
        try:
            result = self._write_folder(build_directory, None)
            self._publish_new_folder(build_directory)
            return result
        finally:
            shutil.rmtree(build_directory, ignore_errors=True)

    def _publish_new_folder(self, source: Path) -> None:
        if self.directory.exists() and not self.overwrite and any(self.directory.iterdir()):
            raise FileExistsError(f"directory is not empty (set overwrite=True): {self.directory}")
        publish_many([(source, self.directory)], overwrite=self.directory.exists())

    def _write_folder(self, directory: Path, existing: DatasetView | None) -> BuildResult:
        # Appends continue the numeric ids already present in the dataset.
        start = existing.sample_count if existing is not None else 0
        data_dir = directory / DATA_DIR
        data_dir.mkdir(exist_ok=True)
        created: list[Path] = []
        copied_files = 0
        copied_bytes = 0

        # Metadata is replaced only after the new data files are complete. On
        # failure the except block removes those new files and leaves the old
        # collection and Parquet tables untouched.
        transaction = Path(tempfile.mkdtemp(prefix=".taco-", dir=directory))
        temp_metadata = transaction / METADATA_DIR
        try:
            tables = MetadataTableWriter(
                self.contract,
                temp_metadata,
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
                        if sample_dir.exists():
                            raise WriterError(f"{sample_dir} already exists")
                        created.append(sample_dir)
                        sample_dir.mkdir()
                        for asset in sample.assets:
                            assert isinstance(asset.source, Path)
                            target = sample_dir if asset.path is None else sample_dir / asset.path
                            if asset.path is None:
                                # A structure=None sample is DATA/<id> itself,
                                # not a file inside a DATA/<id>/ directory.
                                sample_dir.rmdir()
                            copied_bytes += self._copy_asset(asset.source, target)
                            copied_files += 1
                        tables.add_sample(index, sample)
                        progress.update()
                tables.close()
            except BaseException:
                tables.abort()
                raise

            temp_collection = transaction / COLLECTION_FILENAME
            temp_collection.write_text(self._render_collection(tables.summaries), encoding="utf-8")

            # Data is already in place; publishing these two paths together is
            # the commit point that makes the appended samples visible.
            publish_many(
                [
                    (temp_metadata, directory / METADATA_DIR),
                    (temp_collection, directory / COLLECTION_FILENAME),
                ],
                overwrite=True,
            )
        except BaseException:
            for path in created:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(transaction, ignore_errors=True)

        return BuildResult(
            path=self.directory,
            samples=start + self.sample_count,
            data_files=copied_files,
            metadata_files=len(self.contract.levels),
            size=copied_bytes,
        )


__all__ = ["FolderWriter"]
