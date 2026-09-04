from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import SampleError
from .naming import normalize_relative_path

__all__ = ["Asset", "Sample", "SourceLike"]

SourceLike = "str | os.PathLike[str] | bytes | bytearray | memoryview"


@dataclass(frozen=True)
class Asset:
    """One file of a sample.

    ``path`` is the contract-relative location inside the sample (for example
    ``before/B02.tif``). It is ``None`` only for contracts whose
    ``taco:structure`` is ``null``, where the sample itself is the file.

    ``source`` is where the bytes live right now: a local file path, or the
    raw bytes. Inline bytes are materialized into the writer's staging
    directory when the sample is added.
    """

    path: str | None
    source: Path | bytes

    def __init__(self, path: str | None, source: Any) -> None:
        if path is not None:
            try:
                path = normalize_relative_path(path, context="asset path")
            except ValueError as exc:
                raise SampleError(str(exc)) from exc
        if isinstance(source, (bytes, bytearray, memoryview)):
            normalized: Path | bytes = bytes(source)
        elif isinstance(source, (str, os.PathLike)):
            normalized = Path(source).expanduser()
            if not normalized.is_absolute():
                normalized = normalized.resolve()
        else:
            raise SampleError(f"asset source must be a path or bytes, got {type(source).__name__}")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "source", normalized)

    @property
    def is_inline(self) -> bool:
        return isinstance(self.source, bytes)

    def size(self) -> int:
        """Return the payload size in bytes (stat for paths, len for bytes)."""
        if isinstance(self.source, bytes):
            return len(self.source)
        return self.source.stat().st_size

    def with_source(self, source: Path) -> Asset:
        return Asset(self.path, source)


def _coerce_assets(assets: Any) -> tuple[Asset, ...]:
    if isinstance(assets, Asset):
        return (assets,)
    if isinstance(assets, (str, bytes, bytearray, memoryview, os.PathLike)):
        return (Asset(None, assets),)
    if isinstance(assets, Mapping):
        return tuple(Asset(path, source) for path, source in assets.items())
    if isinstance(assets, Sequence):
        result = []
        for item in assets:
            if isinstance(item, Asset):
                result.append(item)
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                result.append(Asset(item[0], item[1]))
            else:
                raise SampleError("assets sequence items must be Asset or (path, source) pairs")
        return tuple(result)
    raise SampleError("assets must be a mapping {path: source}, a sequence of Asset, or a single source")


@dataclass(frozen=True, init=False)
class Sample:
    """A unit waiting to be validated and appended to a writer.

    ``assets`` maps contract paths to sources. For ``taco:structure = null``
    contracts pass a single source instead. ``metadata`` is keyed by contract
    level (``"collection"``, ``"sample"``, ``"sample/<folder>"``); the
    ``collection`` level holds one flat mapping of fields, deeper levels are
    keyed by child name.
    """

    assets: tuple[Asset, ...]
    metadata: dict[str, Any]

    def __init__(self, *, assets: Any, metadata: Mapping[str, Any] | None = None) -> None:
        normalized = _coerce_assets(assets)
        if not normalized:
            raise SampleError("a sample needs at least one asset")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise SampleError("sample metadata must be a mapping keyed by level")
        object.__setattr__(self, "assets", normalized)
        object.__setattr__(self, "metadata", dict(metadata))

    @property
    def single(self) -> bool:
        """True when the sample is one file without internal structure."""
        return len(self.assets) == 1 and self.assets[0].path is None

    def replace_assets(self, assets: Sequence[Asset]) -> Sample:
        return Sample(assets=tuple(assets), metadata=self.metadata)

    def total_size(self) -> int:
        return sum(asset.size() for asset in self.assets)
