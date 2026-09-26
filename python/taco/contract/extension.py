from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pyarrow as pa

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel


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


@dataclass(frozen=True)
class ExtensionContext:
    """One batch of rows and the local asset associated with each row."""

    level: str
    columns: Mapping[str, Sequence[Any]]
    assets: tuple[Path | None, ...]

    def __post_init__(self) -> None:
        lengths = {len(values) for values in self.columns.values()}
        lengths.add(len(self.assets))
        if len(lengths) > 1:
            raise ValueError("extension context columns and assets must have the same length")

    def __len__(self) -> int:
        return len(self.assets)


@dataclass(frozen=True)
class Extension(ABC):
    """Metadata operation executed by the writer after assets are local.

    An extension may have an ``input_model`` supplied with each sample, output
    fields produced by :meth:`run`, or both. Fully-qualified dependencies make
    independently developed extensions composable without a central registry.
    """

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset()
    __taco_complete_level__: ClassVar[bool] = False

    @property
    def input_model(self) -> type[BaseModel] | None:
        return None

    @property
    @abstractmethod
    def requires(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    @abstractmethod
    def fields(self) -> pa.Schema:
        """Unqualified fields produced in the extension's namespace."""
        raise NotImplementedError

    def configuration(self) -> Mapping[str, Any]:
        return {}

    def collection_metadata(self) -> Mapping[str, Any]:
        """Return semantic parameters that must travel with the dataset."""
        return {}

    @abstractmethod
    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        raise NotImplementedError


@dataclass(frozen=True)
class DerivedMetadata(Extension):
    """Compatibility base for column-only extensions.

    New extensions should implement :class:`Extension` directly. This adapter
    keeps existing custom derived metadata working while the contract uses one
    execution model for every active metadata group.
    """

    @abstractmethod
    def compute(self, columns: Mapping[str, Sequence[Any]]) -> Mapping[str, Sequence[Any]]:
        raise NotImplementedError

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return self.compute({name: context.columns[name] for name in self.requires})
