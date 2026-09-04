from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..contract.collection import Collection
from ..contract.sample import Asset, Sample
from ..errors import SampleError, WriterError
from .journal import Journal


class WriterState(str, Enum):
    OPEN = "open"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class BuildResult:
    """Summary returned by a successful writer run."""

    path: Path
    samples: int
    data_files: int
    metadata_files: int
    size: int
    parts: tuple[Path, ...] = ()

    @property
    def partitioned(self) -> bool:
        return bool(self.parts)


class StagedWriter:
    def __init__(
        self,
        collection: Collection,
        *,
        staging_dir: str | os.PathLike[str] | None = None,
        batch_size: int = 10_000,
        row_group_size: int = 65_536,
        parquet_options: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(collection, Collection):
            raise TypeError("collection must be a Collection")
        if row_group_size < 1 or batch_size < 1:
            raise ValueError("row_group_size and batch_size must be positive")

        self.collection = collection
        self.contract = collection.contract
        self.row_group_size = row_group_size
        self.batch_size = batch_size
        self.parquet_options = dict(parquet_options or {})
        self.state = WriterState.OPEN
        self._result: BuildResult | None = None

        if staging_dir is not None:
            Path(staging_dir).mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="taco-", dir=os.fspath(staging_dir) if staging_dir is not None else None
        )
        self._stage = Path(self._temporary.name)
        self._journal = Journal(self._stage / "samples.journal")
        self._inline_dir = self._stage / "inline"

    def __enter__(self) -> StagedWriter:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    @property
    def sample_count(self) -> int:
        return self._journal.count

    @property
    def result(self) -> BuildResult | None:
        return self._result

    def _require_open(self, action: str) -> None:
        if self.state is not WriterState.OPEN:
            raise WriterError(f"{action} is only valid while the writer is open; state={self.state.value}")

    def close(self) -> None:
        """Release staging resources without publishing anything."""
        self._journal.close()
        self._temporary.cleanup()
        if self.state is WriterState.OPEN:
            self.state = WriterState.CLOSED

    def add(self, sample: Sample | Mapping[str, Any]) -> int:
        """Validate and stage one sample, returning its zero-based index."""
        self._require_open("add()")
        if isinstance(sample, Mapping):
            sample = Sample(**sample)
        if not isinstance(sample, Sample):
            raise TypeError("sample must be a Sample or a mapping with assets/metadata")

        sample_id = self._journal.count
        normalized = self._materialize(sample_id, self.contract.validate_sample(sample))
        size = sum(self._source_size(asset) for asset in normalized.assets)
        self._journal.append((normalized, size))
        return sample_id

    def extend(self, samples: Iterable[Sample | Mapping[str, Any]]) -> int:
        for sample in samples:
            self.add(sample)
        return self._journal.count

    def run(self) -> BuildResult:
        """Build once and return the same result on later calls."""
        if self.state is WriterState.SUCCEEDED:
            assert self._result is not None
            return self._result
        self._require_open("run()")
        if self._journal.count == 0:
            raise WriterError("cannot build a dataset without samples")

        self._journal.close()
        self.state = WriterState.RUNNING
        try:
            result = self._build()
        except BaseException:
            self.state = WriterState.FAILED
            raise
        self._result = result
        self.state = WriterState.SUCCEEDED
        return result

    def _build(self) -> BuildResult:
        raise NotImplementedError

    def _materialize(self, sample_id: int, sample: Sample) -> Sample:
        if not any(asset.is_inline for asset in sample.assets):
            return sample

        assets: list[Asset] = []
        for position, asset in enumerate(sample.assets):
            if not asset.is_inline:
                assets.append(asset)
                continue
            name = asset.path if asset.path is not None else f"asset-{position}"
            target = self._inline_dir / str(sample_id) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(asset.source)  # type: ignore[arg-type]
            assets.append(asset.with_source(target))
        return sample.replace_assets(assets)

    @staticmethod
    def _source_size(asset: Asset) -> int:
        source = asset.source
        assert isinstance(source, Path)
        if not source.is_file():
            raise FileNotFoundError(f"asset source is not a regular file: {source}")
        size = source.stat().st_size
        if size == 0:
            raise SampleError(f"zero-byte assets are not allowed: {source}")
        return size

    def _records(self) -> Iterator[tuple[int, Sample, int]]:
        for index, (sample, size) in enumerate(self._journal):
            yield index, sample, size
