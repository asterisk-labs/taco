from typing import ClassVar

from .sample import ISTAC as _SampleISTAC
from .sample import STAC as _SampleSTAC


class STAC(_SampleSTAC):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


class ISTAC(_SampleISTAC):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"folder"})


__all__ = ["ISTAC", "STAC"]
