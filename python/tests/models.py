"""Metadata models shared by the tests."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, field_validator

import taco


class Scoped(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset()


class Split(Scoped):
    __taco_scopes__ = frozenset({"sample"})

    split: Annotated[Literal["train", "test", "validation"], taco.Encoding("dictionary")] = Field(
        description="Dataset split"
    )


class Scaling(Scoped):
    __taco_scopes__ = frozenset({"sample"})

    scale_factor: Annotated[list[float], pa.list_(pa.float32())] | None = Field(
        default=None, description="Multiplicative factors used to unpack values"
    )
    scale_offset: Annotated[list[float], pa.list_(pa.float32())] | None = Field(
        default=None, description="Offsets used to unpack values"
    )

    @field_validator("scale_factor", "scale_offset", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> object:
        return [value] if isinstance(value, int | float) else value


class LabelClass(Scoped):
    name: str
    category: str | int

    @field_serializer("category")
    def _category(self, value: str | int) -> str:
        return str(value)


class Labels(Scoped):
    __taco_scopes__ = frozenset({"collection"})

    classes: list[str | LabelClass]

    @computed_field(description="Number of label classes")
    def num_classes(self) -> int:
        return len(self.classes)


class SpectralBand(Scoped):
    name: str
    index: int | None = None
    center_wavelength: float | None = None


class Optical(Scoped):
    __taco_scopes__ = frozenset({"collection"})

    sensor: str
    bands: list[SpectralBand] = Field(default_factory=list)

    @computed_field(description="Number of spectral bands")
    def num_bands(self) -> int:
        return len(self.bands)


class Publication(Scoped):
    doi: str
    citation: str


class Publications(Scoped):
    __taco_scopes__ = frozenset({"collection"})

    publications: list[Publication]


class SplitStrategy(Scoped):
    __taco_scopes__ = frozenset({"collection"})

    strategy: Literal["random", "stratified", "manual"]
