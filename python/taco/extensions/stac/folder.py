from typing import ClassVar

from .models import STAC as _SampleSTAC
from .models import Point
from .models import Spatial as _SampleSpatial
from .models import Temporal as _SampleTemporal


class Spatial(_SampleSpatial):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


class Temporal(_SampleTemporal):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


class STAC(_SampleSTAC):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


__all__ = ["STAC", "Point", "Spatial", "Temporal"]
