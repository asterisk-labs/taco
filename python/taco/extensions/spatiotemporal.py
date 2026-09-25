from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import pyarrow as pa

from ..metadata._base import Extension, ExtensionContext
from ..metadata.spatiotemporal import STAC as STACMetadata
from ..metadata.spatiotemporal import Spatial as SpatialMetadata
from ..metadata.spatiotemporal import (
    footprint_bbox,
    footprint_center,
    grid_center,
    grid_footprint,
    load_footprint,
    point_wkb,
)


def _column(context: ExtensionContext, name: str) -> Sequence[Any]:
    try:
        return context.columns[name]
    except KeyError as exc:
        raise ValueError(f"extension input {name!r} is unavailable") from exc


def _description(model: type[SpatialMetadata] | type[STACMetadata], name: str) -> dict[bytes, bytes]:
    return {b"description": (model.model_fields[name].description or "").encode()}


def _location_fields(model: type[SpatialMetadata] | type[STACMetadata]) -> pa.Schema:
    return pa.schema(
        [
            pa.field("geometry", pa.binary(), nullable=False, metadata=_description(model, "geometry")),
            pa.field(
                "bbox",
                pa.list_(pa.field("item", pa.float64(), nullable=False)),
                nullable=False,
                metadata=_description(model, "bbox"),
            ),
            pa.field("centroid", pa.binary(), nullable=False, metadata=_description(model, "centroid")),
        ]
    )


def _locate(context: ExtensionContext, namespace: str) -> dict[str, Sequence[Any]]:
    geometries: list[bytes] = []
    boxes: list[list[float]] = []
    centroids: list[bytes] = []
    for geometry, bbox, centroid, code, shape, transform in zip(
        _column(context, f"{namespace}:geometry"),
        _column(context, f"{namespace}:bbox"),
        _column(context, f"{namespace}:centroid"),
        _column(context, f"{namespace}:proj_code"),
        _column(context, f"{namespace}:proj_shape"),
        _column(context, f"{namespace}:proj_transform"),
        strict=True,
    ):
        grid = code is not None and shape is not None and transform is not None
        if geometry is None:
            if not grid:
                raise ValueError(f"{namespace}:geometry is required unless the proj_ fields are given")
            geometry = grid_footprint(code, shape, transform)
        if bbox is None:
            bbox = footprint_bbox(load_footprint(geometry, field=f"{namespace}:geometry"))
        if centroid is None:
            # The grid center is exact; a footprint only approximates the grid.
            if grid:
                centroid = grid_center(code, shape, transform)
            else:
                centroid = point_wkb(*footprint_center(geometry, field=f"{namespace}:geometry"))
        geometries.append(geometry)
        boxes.append([float(value) for value in bbox])
        centroids.append(centroid)
    return {"geometry": geometries, "bbox": boxes, "centroid": centroids}


@dataclass(frozen=True)
class Spatial(Extension):
    """Complete the footprint and bounding box during ``writer.run()``."""

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
        return ("spatial:proj_code", "spatial:proj_shape", "spatial:proj_transform")

    @property
    def fields(self) -> pa.Schema:
        return _location_fields(self.model)

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return _locate(context, "spatial")


@dataclass(frozen=True)
class STAC(Extension):
    """Complete the footprint and bounding box of a STAC group during ``writer.run()``."""

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
        return ("stac:proj_code", "stac:proj_shape", "stac:proj_transform")

    @property
    def fields(self) -> pa.Schema:
        return _location_fields(self.model)

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return _locate(context, "stac")


__all__ = ["STAC", "Spatial"]
