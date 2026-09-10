from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import pyarrow as pa

from . import reader
from ._source import Location, Source, normalize
from .contract.collection import Collection, Extent
from .contract.contract import Contract
from .errors import ContainerError

Layout = Literal["wide", "long"]


def _collection(paths: tuple[Location, ...]) -> Collection:
    collections = tuple(Collection.from_dict(data) for data in reader._collections(paths))
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


class Dataset:
    def __init__(self, source: Source) -> None:
        self.sources = normalize(source)
        self.collection = _collection(self.sources)

    @property
    def contract(self) -> Contract:
        return self.collection.contract

    def __repr__(self) -> str:
        location = f"source={self.sources[0]!r}" if len(self.sources) == 1 else f"sources={len(self.sources)}"
        return f"Dataset({self.collection.id!r}, version={self.collection.dataset_version!r}, {location})"

    def _repr_html_(self) -> str:
        from ._repr import dataset_html

        return dataset_html(self)


def open_dataset(source: Source) -> Dataset:
    return Dataset(source)


def read(
    source: Source | Dataset,
    *,
    layout: Layout = "wide",
    idx: reader.Index = None,
    level: str | None = None,
    files: Sequence[str] | None = None,
    location: bool = True,
) -> pa.Table:
    if layout not in ("wide", "long"):
        raise ValueError("layout must be 'wide' or 'long'")
    if isinstance(source, Dataset):
        sources = source.sources
    else:
        sources = normalize(source)
        if len(sources) > 1:
            sources = open_dataset(sources).sources
    return reader.read(
        sources,
        idx=idx,
        level=level,
        pivoted=layout == "wide",
        files=files,
        location=location,
    )


__all__ = ["Dataset", "Layout", "open_dataset", "read"]
