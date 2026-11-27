from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from ..contract.collection import Collection, Extent
from ..errors import ContainerError
from . import native
from .source import Location, PathInput


def load_collection(path: PathInput | Location) -> dict[str, object]:
    """Load ``COLLECTION.json`` from one dataset."""
    return _decode_collection(native.NativeDataset(path).collection, path)


def _decode_collection(text: str, path: PathInput | Location) -> dict[str, object]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ContainerError(f"COLLECTION.json is not a JSON object: {path}")
    return data


def load_collections(
    paths: Sequence[Location], opened: Sequence[native.NativeDataset] | None = None
) -> list[dict[str, object]]:
    if opened is None:
        return [load_collection(path) for path in paths]
    if len(paths) != len(opened):
        raise ValueError("paths and opened datasets must have the same length")
    return [_decode_collection(dataset.collection, path) for path, dataset in zip(paths, opened, strict=True)]


def _add_counts(values: Sequence[object]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for counts in values:
        if not isinstance(counts, dict) or not all(type(count) is int for count in counts.values()):
            raise ContainerError("split:counts must be an object of sample counts")
        for name, count in counts.items():
            totals[name] = totals.get(name, 0) + count
    return totals


# Besides extent, the collection fields that summarize the rows of each source, so
# the partitions of one build differ in them (SPEC 7.5). A list combines them as
# taco.consolidate does. The reader names them itself: the extensions that define
# them need the writer dependencies.
_SUMMARIES: dict[str, Callable[[Sequence[object]], object]] = {"split:counts": _add_counts}


def _shared(collection: Collection) -> dict[str, object]:
    data = collection.to_dict()
    for name in ("extent", "taco:sources", *_SUMMARIES):
        data.pop(name, None)
    return data


def merge_collections(paths: tuple[Location, ...], opened: Sequence[native.NativeDataset] | None = None) -> Collection:
    """Merge compatible source collections, their extents and summaries."""
    documents = load_collections(paths, opened)
    collections = tuple(Collection.from_dict(data) for data in documents)
    if len(collections) == 1:
        return collections[0]
    if any(collection.sources is not None for collection in collections):
        raise ContainerError("a source list cannot contain TACOCAT datasets")

    expected = _shared(collections[0])
    for path, collection in zip(paths[1:], collections[1:], strict=True):
        if collection.contract.levels != collections[0].contract.levels or _shared(collection) != expected:
            raise ContainerError(f"source does not belong to the same collection: {path}")

    extents = [collection.extent for collection in collections if collection.extent is not None]
    groups: dict[str, dict[str, object]] = {}
    for name, combine in _SUMMARIES.items():
        values = [document[name] for document in documents if document.get(name) is not None]
        if values:
            namespace, _, field = name.partition(":")
            groups[namespace] = {**collections[0].metadata.get(namespace, {}), field: combine(values)}
    return collections[0].replace(extent=Extent.union(extents), **groups)


__all__ = ["load_collection", "load_collections", "merge_collections"]
