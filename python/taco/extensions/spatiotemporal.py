from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import pyarrow as pa

from ..metadata._base import Extension, ExtensionContext
from ..metadata.spatiotemporal import STAC as STACMetadata
from ..metadata.spatiotemporal import Spatial as SpatialMetadata
from ..metadata.spatiotemporal import footprint_center, grid_centers, to_float32

_POINT = pa.struct([pa.field("lon", pa.float32(), nullable=False), pa.field("lat", pa.float32(), nullable=False)])


def _column(context: ExtensionContext, name: str) -> Sequence[Any]:
    try:
        return context.columns[name]
    except KeyError as exc:
        raise ValueError(f"extension input {name!r} is unavailable") from exc


def _centroid_field(model: type[SpatialMetadata] | type[STACMetadata]) -> pa.Schema:
    description = (model.model_fields["centroid"].description or "").encode()
    return pa.schema([pa.field("centroid", _POINT, nullable=False, metadata={b"description": description})])


def _point(longitude: float, latitude: float) -> dict[str, float]:
    return {"lon": to_float32(longitude), "lat": to_float32(latitude)}


def _centroids(context: ExtensionContext, namespace: str) -> dict[str, Sequence[Any]]:
    centroids = list(_column(context, f"{namespace}:centroid"))
    geometries = _column(context, f"{namespace}:geometry")
    codes = _column(context, f"{namespace}:proj_code")
    shapes = _column(context, f"{namespace}:proj_shape")
    transforms = _column(context, f"{namespace}:proj_transform")
    grids = [
        index
        for index, centroid in enumerate(centroids)
        if centroid is None and codes[index] is not None and shapes[index] is not None and transforms[index] is not None
    ]
    centers = grid_centers([codes[i] for i in grids], [shapes[i] for i in grids], [transforms[i] for i in grids])
    for index, center in zip(grids, centers, strict=True):
        centroids[index] = _point(*center)
    for index, centroid in enumerate(centroids):
        if centroid is None:
            if geometries[index] is None:
                raise ValueError(f"{namespace}:geometry is required unless the proj_ fields are given")
            centroids[index] = _point(*footprint_center(geometries[index], field=f"{namespace}:geometry"))
    return {"centroid": centroids}


@dataclass(frozen=True)
class Spatial(Extension):
    """Compute spatial centroids."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample", "folder"})
    model: type[SpatialMetadata] = SpatialMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.model, type) or not issubclass(self.model, SpatialMetadata):
            raise TypeError("Spatial model must inherit taco.metadata.sample.Spatial")

    @property
    def input_model(self) -> type[SpatialMetadata]:
        return self.model

    @property
    def requires(self) -> tuple[str, ...]:
        return ("spatial:geometry", "spatial:proj_code", "spatial:proj_shape", "spatial:proj_transform")

    @property
    def fields(self) -> pa.Schema:
        return _centroid_field(self.model)

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return _centroids(context, "spatial")


@dataclass(frozen=True)
class STAC(Extension):
    """Compute STAC centroids."""

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample", "folder"})
    model: type[STACMetadata] = STACMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.model, type) or not issubclass(self.model, STACMetadata):
            raise TypeError("STAC model must inherit taco.metadata.sample.STAC")

    @property
    def input_model(self) -> type[STACMetadata]:
        return self.model

    @property
    def requires(self) -> tuple[str, ...]:
        return ("stac:geometry", "stac:proj_code", "stac:proj_shape", "stac:proj_transform")

    @property
    def fields(self) -> pa.Schema:
        return _centroid_field(self.model)

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return _centroids(context, "stac")


__all__ = ["STAC", "Spatial"]
