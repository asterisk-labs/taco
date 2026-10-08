from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, ClassVar, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from ....container.parquet import Encoding
from ....contract.extension import CollectionSummary

Partition = Literal["train", "validation", "test", "excluded"]
PARTITIONS: tuple[str, ...] = get_args(Partition)


class Counts(CollectionSummary):
    """Count the samples in each partition, including the empty ones."""

    field = "split:counts"
    requires: ClassVar[tuple[str, ...]] = ("split",)

    def __init__(self) -> None:
        self._counts = dict.fromkeys(PARTITIONS, 0)

    def update(self, columns: Mapping[str, Sequence[Any]]) -> None:
        for value in columns["split"]:
            if value is not None:
                self._counts[value] += 1

    def finish(self) -> dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        pass

    @classmethod
    def merge(cls, values: Sequence[Any]) -> dict[str, int]:
        totals = dict.fromkeys(PARTITIONS, 0)
        for counts in values:
            for name, count in (counts or {}).items():
                totals[name] += count
        return totals


class Split(BaseModel):
    """The partition of one sample."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_namespace__: ClassVar[str | None] = "split"
    __taco_summaries__: ClassVar[tuple[type[CollectionSummary], ...]] = (Counts,)

    split: Annotated[Partition, Encoding("dictionary")] = Field(
        description="Partition of the sample; excluded keeps it out of every partition"
    )


__all__ = ["PARTITIONS", "Counts", "Partition", "Split"]
