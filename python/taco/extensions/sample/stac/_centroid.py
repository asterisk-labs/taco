"""STAC centroid operation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import pyarrow as pa

from ....contract.extension import Extension, ExtensionContext
from ....errors import SampleError
from .models import footprint_center, grid_centers, grid_problems, to_float32

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import _Location

_POINT = pa.struct([pa.field("lon", pa.float32(), nullable=False), pa.field("lat", pa.float32(), nullable=False)])


def _point(longitude: float, latitude: float) -> dict[str, float]:
    return {"lon": to_float32(longitude), "lat": to_float32(latitude)}


def _row_name(context: ExtensionContext, index: int) -> object:
    for name in ("id", "internal:relative_path"):
        values = context.columns.get(name)
        if values is not None and values[index] is not None:
            return values[index]
    return index


@dataclass(frozen=True)
class Centroid(Extension):
    """Derive missing centroids."""

    __taco_row_local__: ClassVar[bool] = True
    model: type[_Location]

    @property
    def namespace(self) -> str:
        return str(self.model.__taco_namespace__)

    @property
    def input_model(self) -> type[_Location]:
        return self.model

    @property
    def requires(self) -> tuple[str, ...]:
        return tuple(f"{self.namespace}:{name}" for name in ("geometry", "proj_code", "proj_shape", "proj_transform"))

    @property
    def fields(self) -> pa.Schema:
        description = (self.model.model_fields["centroid"].description or "").encode()
        return pa.schema([pa.field("centroid", _POINT, nullable=False, metadata={b"description": description})])

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        namespace = self.namespace
        centroids = list(context.columns[f"{namespace}:centroid"])
        geometries = context.columns[f"{namespace}:geometry"]
        codes = context.columns[f"{namespace}:proj_code"]
        shapes = context.columns[f"{namespace}:proj_shape"]
        transforms = context.columns[f"{namespace}:proj_transform"]
        grids = [index for index, code in enumerate(codes) if code is not None]
        problems = grid_problems([codes[i] for i in grids], [shapes[i] for i in grids], [transforms[i] for i in grids])
        if problems:
            index, message = problems[0]
            row = grids[index]
            raise SampleError(f"{namespace} grid of {_row_name(context, row)!r} at {context.level!r}: {message}")
        missing = [index for index in grids if centroids[index] is None]
        centers = grid_centers(
            [codes[i] for i in missing], [shapes[i] for i in missing], [transforms[i] for i in missing]
        )
        for index, center in zip(missing, centers, strict=True):
            centroids[index] = _point(*center)
        for index, centroid in enumerate(centroids):
            if centroid is None and geometries[index] is not None:
                centroids[index] = _point(*footprint_center(geometries[index], field=f"{namespace}:geometry"))
        return {"centroid": centroids}


__all__ = ["Centroid"]
