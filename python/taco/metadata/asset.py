from __future__ import annotations

from typing import Annotated

import pyarrow as pa
from pydantic import Field, field_validator

from ._base import AssetModel

Float32 = Annotated[float, pa.float32()]
Int32 = Annotated[int, pa.int32()]


class Raster(AssetModel):
    resolution: float = Field(gt=0, description="Spatial resolution in meters")
    num_bands: Int32 = Field(gt=0, description="Number of bands")
    data_type: str | None = Field(default=None, description="Raster data type")


class RasterStats(AssetModel):
    stats: Annotated[list[list[float]], pa.list_(pa.list_(pa.float32()))] | None = Field(
        default=None, description="Per-band statistics"
    )


class Scaling(AssetModel):
    scale_factor: Annotated[list[float], pa.list_(pa.float32())] | None = Field(
        default=None, description="Multiplicative factors used to unpack values"
    )
    scale_offset: Annotated[list[float], pa.list_(pa.float32())] | None = Field(
        default=None, description="Offsets used to unpack values"
    )
    padding: Annotated[list[int], pa.list_(pa.int32())] | None = Field(
        default=None, description="Padding as top, right, bottom, left"
    )

    @field_validator("scale_factor", "scale_offset", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> object:
        if isinstance(value, int | float):
            return [value]
        return value

    @field_validator("scale_factor")
    @classmethod
    def _nonzero(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and any(item == 0 for item in value):
            raise ValueError("scale_factor cannot contain zero")
        return value

    @field_validator("padding")
    @classmethod
    def _padding(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) != 4:
            raise ValueError("padding must contain top, right, bottom, left")
        return value


__all__ = ["Raster", "RasterStats", "Scaling"]
