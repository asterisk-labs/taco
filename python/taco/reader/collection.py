from __future__ import annotations

import json
from collections.abc import Sequence

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


def merge_collections(paths: tuple[Location, ...], opened: Sequence[native.NativeDataset] | None = None) -> Collection:
    """Merge compatible source collections and their extents."""
    collections = tuple(Collection.from_dict(data) for data in load_collections(paths, opened))
    if len(collections) == 1:
        return collections[0]
    if any(collection.sources is not None for collection in collections):
        raise ContainerError("a source list cannot contain TACOCAT datasets")

    expected = collections[0].to_dict()
    expected.pop("extent", None)
    expected.pop("taco:sources", None)
    for path, collection in zip(paths[1:], collections[1:], strict=True):
        actual = collection.to_dict()
        actual.pop("extent", None)
        actual.pop("taco:sources", None)
        if collection.contract.levels != collections[0].contract.levels or actual != expected:
            raise ContainerError(f"source does not belong to the same collection: {path}")

    extents = [collection.extent for collection in collections if collection.extent is not None]
    return collections[0].replace(extent=Extent.union(extents))


__all__ = ["load_collection", "load_collections", "merge_collections"]
