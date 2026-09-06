from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TypeAlias, cast

from ..errors import SampleError
from ..schema import Metadata
from .naming import normalize_relative_path

SourceLike: TypeAlias = str | PathLike[str] | bytes | bytearray | memoryview


def _source(value: SourceLike) -> Path | bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, (str, PathLike)):
        return Path(value).expanduser().resolve()
    raise SampleError(f"source must be a path or bytes, got {type(value).__name__}")


@dataclass(frozen=True, init=False)
class Asset:
    source: Path | bytes
    path: str | None
    metadata: Metadata

    def __init__(
        self,
        source: SourceLike,
        *,
        path: str | None = None,
        metadata: Metadata | None = None,
    ) -> None:
        if path is not None:
            try:
                path = normalize_relative_path(path, context="asset path")
            except ValueError as exc:
                raise SampleError(str(exc)) from exc
        if metadata is not None and not isinstance(metadata, Metadata):
            raise SampleError("asset metadata must be taco.Metadata")
        object.__setattr__(self, "source", _source(source))
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "metadata", metadata or Metadata())

    @property
    def is_inline(self) -> bool:
        return isinstance(self.source, bytes)

    def replace(self, *, source: Path | bytes | None = None, path: str | None = None) -> Asset:
        actual_source = self.source if source is None else source
        actual_path = self.path if path is None else path
        return Asset(actual_source, path=actual_path, metadata=self.metadata)


@dataclass(frozen=True, init=False)
class Folder:
    path: str
    metadata: Metadata

    def __init__(self, path: str, *, metadata: Metadata) -> None:
        try:
            normalized = normalize_relative_path(path, context="folder path")
        except ValueError as exc:
            raise SampleError(str(exc)) from exc
        if not isinstance(metadata, Metadata):
            raise SampleError("folder metadata must be taco.Metadata")
        object.__setattr__(self, "path", normalized)
        object.__setattr__(self, "metadata", metadata)


AssetInput: TypeAlias = SourceLike | Asset | Sequence[Asset | SourceLike]


def _assets(values: AssetInput) -> tuple[Asset, ...]:
    if isinstance(values, Asset):
        return (values,)
    if isinstance(values, (str, bytes, bytearray, memoryview, PathLike)):
        return (Asset(values),)
    if isinstance(values, Sequence):
        return tuple(item if isinstance(item, Asset) else Asset(cast(SourceLike, item)) for item in values)
    raise SampleError("assets must be an Asset, a source, or a sequence of them")


@dataclass(frozen=True, init=False)
class Sample:
    assets: tuple[Asset, ...]
    metadata: Metadata
    folders: tuple[Folder, ...]

    def __init__(
        self,
        *,
        assets: AssetInput | Sequence[Asset] = (),
        metadata: Metadata | None = None,
        folders: Sequence[Folder] = (),
    ) -> None:
        if metadata is not None and not isinstance(metadata, Metadata):
            raise SampleError("sample metadata must be taco.Metadata")
        if isinstance(folders, (str, bytes)) or not isinstance(folders, Sequence):
            raise SampleError("folders must be a sequence of taco.Folder")
        normalized_folders = tuple(folders)
        if not all(isinstance(folder, Folder) for folder in normalized_folders):
            raise SampleError("folders must contain taco.Folder objects")
        paths = [folder.path for folder in normalized_folders]
        if len(paths) != len(set(paths)):
            raise SampleError("a folder appears more than once")
        object.__setattr__(self, "assets", _assets(assets))
        object.__setattr__(self, "metadata", metadata or Metadata())
        object.__setattr__(self, "folders", normalized_folders)

    def replace_assets(self, assets: Sequence[Asset]) -> Sample:
        return Sample(assets=assets, metadata=self.metadata, folders=self.folders)


@dataclass(frozen=True)
class _PreparedAsset:
    source: Path | bytes
    path: str | None

    @property
    def is_inline(self) -> bool:
        return isinstance(self.source, bytes)

    def replace_source(self, source: Path) -> _PreparedAsset:
        return _PreparedAsset(source, self.path)


@dataclass(frozen=True)
class _PreparedNode:
    name: str
    is_folder: bool
    metadata: dict[str, object]


@dataclass(frozen=True)
class _PreparedSample:
    assets: tuple[_PreparedAsset, ...]
    metadata: dict[str, object]
    rows: dict[str, tuple[_PreparedNode, ...]]

    def replace_assets(self, assets: Sequence[_PreparedAsset]) -> _PreparedSample:
        return _PreparedSample(tuple(assets), self.metadata, self.rows)

    def replace_metadata(self, metadata: dict[str, object]) -> _PreparedSample:
        return _PreparedSample(self.assets, metadata, self.rows)


__all__ = ["Asset", "Folder", "Sample", "SourceLike"]
