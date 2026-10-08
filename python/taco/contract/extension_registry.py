"""Built-in extension registry."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import cache
from importlib.resources import files
from typing import Any

BASE = "https://asterisk.coop/taco/spec/extensions"
BUILTINS = {
    "stac": (f"{BASE}/stac/v1.0.0/schema.json", ("temporal", "spatial", "stac")),
    "rumi": (f"{BASE}/rumi/v1.0.0/schema.json", ("rumi",)),
    "majortom": (f"{BASE}/majortom/v1.0.0/schema.json", ("majortom",)),
    "geoenrich": (f"{BASE}/geoenrich/v1.0.0/schema.json", ("geoenrich",)),
    "split": (f"{BASE}/split/v1.0.0/schema.json", ("split",)),
}
OWNERS = {namespace: identifier for identifier, namespaces in BUILTINS.values() for namespace in namespaces}

_IDENTIFIER = re.compile(rf"{re.escape(BASE)}/(?P<name>[a-z][a-z0-9_]*)/v[0-9]+\.[0-9]+\.[0-9]+/schema\.json")


def builtin_name(identifier: str) -> str | None:
    match = _IDENTIFIER.fullmatch(identifier)
    if match is None:
        return None
    name = match.group("name")
    return name if name in BUILTINS else None


def used_namespaces(document: Mapping[str, Any]) -> set[str]:
    names = [key for key in document if ":" in key]
    for fields in document.get("taco:metadata", {}).values():
        names.extend(fields)
    return {name.partition(":")[0] for name in names}


def declared(document: Mapping[str, Any]) -> list[str]:
    identifiers = set(document.get("taco:extensions", ()))
    names = {name for identifier in identifiers if (name := builtin_name(identifier)) is not None}
    for namespace in used_namespaces(document):
        owner = OWNERS.get(namespace)
        if owner is None:
            continue
        name = builtin_name(owner)
        assert name is not None
        if name not in names:
            identifiers.add(owner)
            names.add(name)
    return sorted(identifiers)


@cache
def schema(identifier: str) -> dict[str, Any] | None:
    """Return a bundled schema."""
    name = next((name for name, (known, _) in BUILTINS.items() if known == identifier), None)
    if name is None:
        return None
    text = files("taco").joinpath("extensions", "schemas", f"{name}.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded
