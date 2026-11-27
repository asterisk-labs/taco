"""Built-in extension registry."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from types import ModuleType
from typing import Any

BASE = "https://asterisk.coop/taco/spec/extensions"


@dataclass(frozen=True)
class Builtin:
    name: str
    version: str
    namespaces: tuple[str, ...]

    @property
    def identifier(self) -> str:
        return f"{BASE}/{self.name}/v{self.version}/schema.json"

    @property
    def module(self) -> str:
        return f"taco.extensions._builtin.{self.name}"


BUILTINS = {
    builtin.name: builtin
    for builtin in (
        Builtin("stac", "1.0.0", ("temporal", "spatial", "stac")),
        Builtin("rumi", "1.0.0", ("rumi",)),
        Builtin("majortom", "1.0.0", ("majortom",)),
        Builtin("geoenrich", "1.0.0", ("geoenrich",)),
        Builtin("split", "1.0.0", ("split",)),
        Builtin("instrument", "1.0.0", ("instrument",)),
        Builtin("labels", "1.0.0", ("labels",)),
        Builtin("ml", "1.0.0", ("ml",)),
    )
}
OWNERS = {namespace: builtin.identifier for builtin in BUILTINS.values() for namespace in builtin.namespaces}
_BY_IDENTIFIER = {builtin.identifier: builtin for builtin in BUILTINS.values()}


def unbundled(identifiers: Iterable[str]) -> list[str]:
    """Return TACO extension identifiers absent from this installation."""
    return [
        identifier
        for identifier in identifiers
        if identifier.startswith(f"{BASE}/") and identifier not in _BY_IDENTIFIER
    ]


def used_namespaces(document: Mapping[str, Any]) -> set[str]:
    names = [key for key in document if ":" in key]
    for fields in document.get("taco:metadata", {}).values():
        names.extend(fields)
    return {name.partition(":")[0] for name in names}


def used_builtins(document: Mapping[str, Any]) -> list[Builtin]:
    """Return the built-ins used by a collection document."""
    identifiers = {OWNERS[namespace] for namespace in used_namespaces(document) if namespace in OWNERS}
    return sorted((_BY_IDENTIFIER[identifier] for identifier in identifiers), key=lambda builtin: builtin.name)


def declared(document: Mapping[str, Any]) -> list[str]:
    identifiers = set(document.get("taco:extensions", ()))
    identifiers.update(builtin.identifier for builtin in used_builtins(document))
    return sorted(identifiers)


def load(builtin: Builtin) -> ModuleType:
    """Import a built-in package."""
    return importlib.import_module(builtin.module)


def models() -> dict[str, type]:
    """Return built-in models by namespace."""
    return {namespace: model for builtin in BUILTINS.values() for namespace, model in load(builtin).MODELS.items()}


def bindings() -> dict[str, type]:
    """Return the built-in model or extension class that binds each namespace."""
    found: dict[str, type] = {}
    for builtin in BUILTINS.values():
        module = load(builtin)
        for name in module.__all__:
            value = getattr(module, name)
            namespace = getattr(value, "__taco_namespace__", None) if isinstance(value, type) else None
            if namespace in builtin.namespaces:
                found[namespace] = value
    return found


def checks(builtin: Builtin) -> Callable[[Any], list[tuple[str, str]]] | None:
    """Return a built-in's dataset check, if it has one."""
    check: Callable[[Any], list[tuple[str, str]]] | None = getattr(load(builtin), "check_dataset", None)
    return check


@cache
def schema(identifier: str) -> dict[str, Any] | None:
    """Return a bundled schema."""
    builtin = _BY_IDENTIFIER.get(identifier)
    if builtin is None:
        return None
    text = files(builtin.module).joinpath("schema.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded
