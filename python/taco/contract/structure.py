from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ..errors import ContractError
from ..schema import Metadata
from .naming import normalize_relative_path, validate_component, variable_sequences_overlap
from .sample import Asset

_VARIABLE_LEAF = re.compile(
    r"^(?P<prefix>[^*\[\]]+)\*\[(?P<minimum>\d+)\s*,\s*(?P<maximum>\d+)\](?P<suffix>[^*\[\]]*)$"
)


@dataclass(frozen=True)
class Leaf:
    declaration: str
    folder: tuple[str, ...]
    name: str
    prefix: str | None = None
    minimum: int = 1
    maximum: int = 1
    suffix: str = ""

    @property
    def variable(self) -> bool:
        return self.prefix is not None

    @property
    def identifier(self) -> str:
        return self.prefix if self.prefix is not None else self.name

    def match_index(self, basename: str) -> int | None:
        if not self.variable:
            return 0 if basename == self.name else None
        assert self.prefix is not None
        if not basename.startswith(self.prefix) or not basename.endswith(self.suffix):
            return None
        stop = len(basename) - len(self.suffix)
        middle = basename[len(self.prefix) : stop]
        if not middle.isascii() or not middle.isdigit() or (len(middle) > 1 and middle[0] == "0"):
            return None
        index = int(middle)
        return index if index < self.maximum else None


@dataclass(frozen=True)
class Node:
    name: str
    is_folder: bool
    metadata: Metadata
    asset: Asset | None = None
    leaf: Leaf | None = None
    index: int | None = None


def parse_leaf(declaration: str) -> Leaf:
    if not isinstance(declaration, str):
        raise ContractError(f"structure entries must be strings, got {declaration!r}")
    declaration = normalize_relative_path(declaration, context="structure path", allow_glob=True)
    parts = PurePosixPath(declaration).parts
    folder, basename = parts[:-1], parts[-1]
    match = _VARIABLE_LEAF.match(basename)
    if match is None:
        if any(char in basename for char in "*[]"):
            raise ContractError(f"malformed variable leaf {declaration!r}; expected prefix*[min,max].ext")
        return Leaf(declaration, folder, basename)
    prefix = match.group("prefix")
    suffix = match.group("suffix")
    minimum = int(match.group("minimum"))
    maximum = int(match.group("maximum"))
    validate_component(prefix, context="variable leaf prefix")
    if suffix:
        validate_component("x" + suffix, context="variable leaf suffix")
    if minimum > maximum:
        raise ContractError(f"variable leaf {declaration!r} has min > max")
    if maximum == 0:
        raise ContractError(f"variable leaf {declaration!r} can never produce a file")
    return Leaf(declaration, folder, basename, prefix, minimum, maximum, suffix)


def _check_ambiguity(folder: tuple[str, ...], fixed_names: list[str], variables: list[Leaf]) -> None:
    where = "/".join(folder) or "the sample root"
    for name in fixed_names:
        for variable in variables:
            if variable.match_index(name) is not None:
                raise ContractError(f"{name!r} overlaps {variable.declaration!r} under {where}")
    for index, first in enumerate(variables):
        for second in variables[index + 1 :]:
            assert first.prefix is not None
            assert second.prefix is not None
            if variable_sequences_overlap(
                (first.prefix, first.suffix, first.maximum),
                (second.prefix, second.suffix, second.maximum),
            ):
                raise ContractError(f"{first.declaration!r} overlaps {second.declaration!r} under {where}")


def build_tree(leaves: tuple[Leaf, ...]) -> dict[tuple[str, ...], tuple[tuple[str, Any], ...]]:
    children: dict[tuple[str, ...], list[tuple[str, Any]]] = {(): []}
    declarations: set[str] = set()
    for leaf in leaves:
        if leaf.declaration in declarations:
            raise ContractError(f"structure declares {leaf.declaration!r} twice")
        declarations.add(leaf.declaration)
        for depth, name in enumerate(leaf.folder):
            parent = leaf.folder[:depth]
            entries = children.setdefault(parent, [])
            if ("folder", name) not in entries:
                entries.append(("folder", name))
            children.setdefault(leaf.folder[: depth + 1], [])
        children[leaf.folder].append(("leaf", leaf))

    for folder, entries in children.items():
        identifiers = [item if kind == "folder" else item.identifier for kind, item in entries]
        if len(identifiers) != len(set(identifiers)):
            where = "/".join(folder) or "the sample root"
            raise ContractError(f"children under {where} must have distinct identifiers")
        fixed_names = [
            item if kind == "folder" else item.name for kind, item in entries if kind == "folder" or not item.variable
        ]
        variables = [item for kind, item in entries if kind == "leaf" and item.variable]
        _check_ambiguity(folder, fixed_names, variables)
    return {folder: tuple(entries) for folder, entries in children.items()}


__all__ = ["Leaf", "Node", "build_tree", "parse_leaf"]
