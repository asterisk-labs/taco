from typing import ClassVar

from .sample import STAC as _SampleSTAC
from .sample import Point
from .sample import Spatial as _SampleSpatial
from .sample import Temporal as _SampleTemporal


class Spatial(_SampleSpatial):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


class Temporal(_SampleTemporal):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


class STAC(_SampleSTAC):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


__all__ = ["STAC", "Point", "Spatial", "Temporal"]
