from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from ..contract.collection import Collection
from ..contract.sample import Sample, _PreparedAsset, _PreparedSample
from ..errors import SampleError, WriterError
from .progress import Progress
from .staging import StagedSamples


@dataclass(frozen=True)
class BuildResult:
    path: Path
    samples: int
    data_files: int
    metadata_files: int
    size: int
    parts: tuple[Path, ...] = ()


class Writer:
    def __init__(
        self,
        collection: Collection,
        *,
        batch_size: int = 10_000,
        row_group_size: int = 65_536,
        parquet_options: Mapping[str, Any] | None = None,
        progress: bool = False,
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
        self.progress = progress

        self.state = "open"
        self._result: BuildResult | None = None
        self._temporary = tempfile.TemporaryDirectory(prefix="taco-")
        self._stage = Path(self._temporary.name)

        # ZIP creation needs to walk the samples more than once. Keeping the
        # prepared objects on disk avoids growing memory with the dataset.
        self._samples: StagedSamples[tuple[_PreparedSample, int]] = StagedSamples(self._stage / "samples.stage")
        self._inline_assets = self._stage / "inline-assets"

    def __enter__(self) -> Writer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def sample_count(self) -> int:
        return self._samples.count

    @property
    def result(self) -> BuildResult | None:
        return self._result

    def _require_open(self, action: str) -> None:
        if self.state != "open":
            raise WriterError(f"{action} is only valid while the writer is open; state={self.state}")

    def close(self) -> None:
        self._samples.close()
        self._temporary.cleanup()
        if self.state == "open":
            self.state = "closed"

    def add(self, sample: Sample) -> int:
        self._require_open("add()")
        if not isinstance(sample, Sample):
            raise TypeError("sample must be a taco.Sample")

        sample_id = self.sample_count
        prepared = self.contract.prepare_sample(sample)
        prepared = self._materialize_inline_assets(sample_id, prepared)
        data_size = sum(self._asset_size(asset) for asset in prepared.assets)
        self._samples.append((prepared, data_size))
        return sample_id

    def extend(self, samples: Iterable[Sample]) -> int:
        for sample in samples:
            self.add(sample)
        return self.sample_count

    def run(self) -> BuildResult:
        # Returning the same result makes run() safe to call from cleanup or
        # orchestration code without publishing the dataset twice.
        if self.state == "succeeded":
            assert self._result is not None
            return self._result
        self._require_open("run()")
        if self.sample_count == 0:
            raise WriterError("cannot build a dataset without samples")

        self._samples.close()
        self.state = "running"
        try:
            self._result = self._build()
        except BaseException:
            self.state = "failed"
            raise
        self.state = "succeeded"
        return self._result

    def _build(self) -> BuildResult:
        raise NotImplementedError

    def _show_progress(
        self,
        total: int,
        description: str,
        unit: str = "sample",
        *,
        enabled: bool = True,
    ) -> Progress:
        return Progress(self.progress and enabled, total, description, unit)

    def _render_collection(self, summaries: Mapping[str, Any]) -> str:
        data = self.collection.to_dict()

        # The freshly computed summary wins over a possibly stale extent from
        # the input collection. If no profile produced one, it stays absent.
        data.pop("extent", None)
        for name, value in summaries.items():
            if value is None:
                data.pop(name, None)
            else:
                data[name] = value
        return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    def _materialize_inline_assets(self, sample_id: int, sample: _PreparedSample) -> _PreparedSample:
        if not any(asset.is_inline for asset in sample.assets):
            return sample

        # From this point on both writers can treat every asset uniformly as a
        # file. The temporary copy disappears together with the writer stage.
        assets: list[_PreparedAsset] = []
        for position, asset in enumerate(sample.assets):
            if isinstance(asset.source, Path):
                assets.append(asset)
                continue
            name = asset.path if asset.path is not None else f"asset-{position}"
            target = self._inline_assets / str(sample_id) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(asset.source)
            assets.append(asset.replace_source(target))
        return sample.replace_assets(assets)

    @staticmethod
    def _asset_size(asset: _PreparedAsset) -> int:
        source = asset.source
        assert isinstance(source, Path)
        if not source.is_file():
            raise FileNotFoundError(f"asset source is not a regular file: {source}")
        size = source.stat().st_size
        if size == 0:
            raise SampleError(f"zero-byte assets are not allowed: {source}")
        return size

    def _staged_samples(self) -> Iterator[tuple[int, _PreparedSample, int]]:
        for sample_id, (sample, size) in enumerate(self._samples):
            yield sample_id, sample, size


__all__ = ["BuildResult", "Writer"]
