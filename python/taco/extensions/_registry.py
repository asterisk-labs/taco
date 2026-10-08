"""Built-in extension schemas and row scopes."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any

from . import geoenrich, majortom, rumi, stac
from .stac.extension import STAC

BUILTIN = {module.IDENTIFIER: module for module in (stac, rumi, majortom, geoenrich)}
SCOPES = {
    stac.IDENTIFIER: STAC.__taco_scopes__,
    rumi.IDENTIFIER: rumi.Rumi.__taco_scopes__,
    majortom.IDENTIFIER: majortom.MajorTOM.__taco_scopes__,
    geoenrich.IDENTIFIER: geoenrich.GeoEnrich.__taco_scopes__,
}


@cache
def schema(identifier: str) -> dict[str, Any] | None:
    """Load a bundled extension schema."""
    module = BUILTIN.get(identifier)
    if module is None:
        return None
    loaded: dict[str, Any] = json.loads(files(module.__name__).joinpath("schema.json").read_text(encoding="utf-8"))
    return loaded
