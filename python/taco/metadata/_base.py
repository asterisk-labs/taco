from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import pyarrow as pa
from pydantic import BaseModel, ConfigDict


class CollectionSummary(ABC):
    field: ClassVar[str]
    requires: ClassVar[tuple[str, ...]]

    @abstractmethod
    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def finish(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class ScopedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset()
    __taco_summaries__: ClassVar[tuple[type[CollectionSummary], ...]] = ()


class SampleModel(ScopedModel):
    __taco_scopes__ = frozenset({"sample"})


class FolderModel(ScopedModel):
    __taco_scopes__ = frozenset({"folder"})


class AssetModel(ScopedModel):
    __taco_scopes__ = frozenset({"asset"})


class CollectionModel(ScopedModel):
    __taco_scopes__ = frozenset({"collection"})


@dataclass(frozen=True)
class DerivedMetadata(ABC):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset()

    @property
    @abstractmethod
    def requires(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    @abstractmethod
    def fields(self) -> pa.Schema:
        raise NotImplementedError

    def configuration(self) -> Mapping[str, Any]:
        return {}

    @abstractmethod
    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        raise NotImplementedError
