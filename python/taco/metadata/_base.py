from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

# Keep the old import path for extension authors.
from ..contract.extension import CollectionSummary, DerivedMetadata, Extension, ExtensionContext


class ScopedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset()
    __taco_namespace__: ClassVar[str | None] = None
    __taco_summaries__: ClassVar[tuple[type[CollectionSummary], ...]] = ()


class SampleModel(ScopedModel):
    __taco_scopes__ = frozenset({"sample"})


class AssetModel(ScopedModel):
    __taco_scopes__ = frozenset({"asset"})


class CollectionModel(ScopedModel):
    __taco_scopes__ = frozenset({"collection"})


__all__ = [
    "AssetModel",
    "CollectionModel",
    "CollectionSummary",
    "DerivedMetadata",
    "Extension",
    "ExtensionContext",
    "SampleModel",
    "ScopedModel",
]
