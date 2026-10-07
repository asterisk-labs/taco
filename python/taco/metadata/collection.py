from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field, field_serializer

from ._base import CollectionModel, ScopedModel


class LabelClass(ScopedModel):
    name: str
    category: str | int
    description: str | None = None

    @field_serializer("category")
    def _category(self, value: str | int) -> str:
        return str(value)


class Labels(CollectionModel):
    classes: list[str | LabelClass]
    description: str | None = None

    @computed_field(description="Number of label classes")
    def num_classes(self) -> int:
        return len(self.classes)


class SpectralBand(ScopedModel):
    name: str
    index: int | None = None
    common_name: str | None = None
    description: str | None = None
    unit: str | None = None
    center_wavelength: float | None = None
    full_width_half_max: float | None = None


class Optical(CollectionModel):
    sensor: str
    bands: list[SpectralBand] = Field(default_factory=list)

    @computed_field(description="Number of spectral bands")
    def num_bands(self) -> int:
        return len(self.bands)


class Publication(ScopedModel):
    doi: str
    citation: str
    summary: str | None = None


class Publications(CollectionModel):
    publications: list[Publication]


class SplitStrategy(CollectionModel):
    strategy: Literal["random", "stratified", "manual", "other", "none", "unknown"]
    rule: str | None = Field(
        default=None,
        description="Versioned rule that produced `split`, as `<namespace>/<rule>-v<n>`",
    )
    group_key: str | None = Field(
        default=None,
        description="Column whose values are kept whole on one side of the split",
    )
    noleak_rule: str | None = Field(
        default=None,
        description="Versioned rule that produced `split_noleak`, when every sample shares it",
    )
    description: str | None = Field(
        default=None,
        description="What the rule does, in words, and why this collection needed it",
    )


__all__ = [
    "LabelClass",
    "Labels",
    "Optical",
    "Publication",
    "Publications",
    "SpectralBand",
    "SplitStrategy",
]
