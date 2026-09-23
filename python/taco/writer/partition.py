from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..container.publish import publish_many
from ..contract.contract import SAMPLE_LEVEL
from ..contract.naming import sanitize_filename
from ..contract.sample import _PreparedSample
from ..errors import WriterError
from .base import BuildResult
from .catalog import consolidate
from .staging import StagedSamples

if TYPE_CHECKING:
    from .archive import ArchiveWriter

logger = logging.getLogger("taco")


def _stage_partitions(writer: ArchiveWriter) -> list[tuple[str, StagedSamples[tuple[_PreparedSample, int]]]]:
    directory = writer._stage / "partitions"
    directory.mkdir()
    partitions: list[tuple[str, StagedSamples[tuple[_PreparedSample, int]]]] = []
    labels: dict[str, Any] = {}

    try:
        if writer.partition_by is not None:
            streams: dict[str, StagedSamples[tuple[_PreparedSample, int]]] = {}
            for sample, size in _samples_with_partition_metadata(writer):
                value = sample.metadata[writer.partition_by]
                label = sanitize_filename(str(value))
                if label in labels and labels[label] != value:
                    raise WriterError(
                        f"partition values {labels[label]!r} and {value!r} collide on file name {label!r}"
                    )
                labels[label] = value
                if label not in streams:
                    stream: StagedSamples[tuple[_PreparedSample, int]] = StagedSamples(
                        directory / f"{len(streams)}.stage"
                    )
                    streams[label] = stream
                    partitions.append((label, stream))
                streams[label].append((sample, size))
        else:
            assert writer.partition_size is not None
            current: StagedSamples[tuple[_PreparedSample, int]] | None = None
            current_size = 0
            for _, sample, size in writer._staged_samples():
                if current is None or (current.count and current_size + size > writer.partition_size):
                    current = StagedSamples(directory / f"{len(partitions)}.stage")
                    partitions.append((f"part{len(partitions) + 1:04d}", current))
                    current_size = 0
                current.append((sample, size))
                current_size += size
    except BaseException:
        for _, stream in partitions:
            stream.close()
        raise

    for _, stream in partitions:
        stream.close()
    return partitions


def _samples_with_partition_metadata(writer: ArchiveWriter) -> Iterator[tuple[_PreparedSample, int]]:
    assert writer.partition_by is not None
    derived = any(
        writer.partition_by in descriptor["produces"]
        for descriptor in writer.contract.extensions.get(SAMPLE_LEVEL, {}).values()
    )
    if not derived:
        for _, sample, size in writer._staged_samples():
            yield sample, size
        return

    batch: list[tuple[_PreparedSample, int]] = []
    for _, sample, size in writer._staged_samples():
        batch.append((sample, size))
        if len(batch) == writer.batch_size:
            yield from _apply_partition_metadata(writer, batch)
            batch = []
    yield from _apply_partition_metadata(writer, batch)


def _apply_partition_metadata(
    writer: ArchiveWriter, batch: list[tuple[_PreparedSample, int]]
) -> Iterator[tuple[_PreparedSample, int]]:
    rows = [dict(sample.metadata) for sample, _ in batch]
    assets = []
    single_fixed_file = len(writer.contract.leaves) == 1 and not writer.contract.leaves[0].variable
    for sample, _ in batch:
        source = sample.assets[0].source if single_fixed_file else None
        assert source is None or isinstance(source, Path)
        assets.append(source)
    writer.contract.apply_extensions(SAMPLE_LEVEL, rows, assets=assets)
    for (sample, size), metadata in zip(batch, rows, strict=True):
        yield sample.replace_metadata(metadata), size


def _write_partition(
    writer: ArchiveWriter,
    release: Path,
    output: Path,
    label: str,
    samples: StagedSamples[tuple[_PreparedSample, int]],
    show_progress: bool,
) -> BuildResult:
    def records() -> Iterator[tuple[int, _PreparedSample]]:
        for index, (sample, _) in enumerate(samples):
            yield index, sample

    logger.info("building partition %s with %d samples -> %s", label, samples.count, output)
    return writer._write_archive(release / output.name, records, samples.count, show_progress=show_progress)


def write_partitioned(writer: ArchiveWriter) -> BuildResult:
    partitions = _stage_partitions(writer)
    if len(partitions) == 1:
        return writer._write_single_archive()

    stem = writer.output.stem
    parent = writer.output.parent
    outputs = [parent / f"{stem}_{label}.zip" for label, _ in partitions]
    for output in outputs:
        writer._validate_output(output)
    tacocat_dir = parent / ".tacocat"
    if tacocat_dir.exists() and not writer.overwrite:
        raise FileExistsError(f"{tacocat_dir} already exists (set overwrite=True)")
    if tacocat_dir.exists() and not tacocat_dir.is_dir():
        raise WriterError(f"TACOCAT output exists and is not a directory: {tacocat_dir}")

    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{stem}.release-", dir=parent) as name:
        release = Path(name)
        jobs = list(zip(outputs, partitions, strict=True))
        if writer.workers == 1:
            results = [
                _write_partition(writer, release, output, label, stream, True) for output, (label, stream) in jobs
            ]
        else:
            results = []
            with (
                writer._show_progress(writer.sample_count, f"building {writer.output.name}") as progress,
                ThreadPoolExecutor(max_workers=min(writer.workers, len(jobs))) as executor,
            ):
                futures = [
                    executor.submit(_write_partition, writer, release, output, label, stream, False)
                    for output, (label, stream) in jobs
                ]
                for future in futures:
                    result = future.result()
                    results.append(result)
                    progress.update(result.samples)

        tacocat = consolidate(
            [item.path for item in results],
            release,
            row_group_size=writer.row_group_size,
            parquet_options=writer.parquet_options,
        )
        replacements = [(result.path, output) for result, output in zip(results, outputs, strict=True)]
        replacements.append((tacocat, tacocat_dir))
        publish_many(replacements, overwrite=writer.overwrite)
    return BuildResult(
        path=tacocat_dir,
        samples=sum(item.samples for item in results),
        data_files=sum(item.data_files for item in results),
        metadata_files=len(writer.contract.levels),
        size=sum(item.size for item in results),
        parts=tuple(outputs),
    )


__all__ = ["write_partitioned"]
