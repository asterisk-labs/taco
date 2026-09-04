from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import pyarrow as pa

from ..errors import ContractError, SampleError
from .naming import level_folder, normalize_relative_path, validate_component, validate_field_name
from .sample import Asset, Sample
from .types import coerce_value, parse_type, type_name

__all__ = ["Contract", "FieldSpec", "Leaf", "MetadataSchema", "Node"]

_VARIABLE_LEAF = re.compile(
    r"^(?P<prefix>[^*\[\]]+)\*\[(?P<minimum>\d+)\s*,\s*(?P<maximum>\d+)\](?P<suffix>[^*\[\]]*)$"
)

FieldSpec = "Sequence[str] | str | pa.DataType"
MetadataSchema = "Mapping[str, Mapping[str, FieldSpec]]"

COLLECTION_LEVEL = "collection"
SAMPLE_LEVEL = "sample"


@dataclass(frozen=True)
class Leaf:
    """One entry of ``taco:structure``: a fixed file or a variable family."""

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
        """Sibling-unique identifier: literal name, or the variable prefix."""
        return self.prefix if self.prefix is not None else self.name

    def instance_name(self, index: int) -> str:
        if not self.variable:
            return self.name
        return f"{self.prefix}{index}{self.suffix}"

    def match_index(self, basename: str) -> int | None:
        """Return the cardinal index if ``basename`` instantiates this leaf."""
        if not self.variable:
            return 0 if basename == self.name else None
        assert self.prefix is not None
        if not basename.startswith(self.prefix) or not basename.endswith(self.suffix):
            return None
        stop = len(basename) - len(self.suffix)
        middle = basename[len(self.prefix) : stop]
        if not middle.isdigit() or (len(middle) > 1 and middle[0] == "0"):
            return None
        return int(middle)


@dataclass(frozen=True)
class Node:
    """A child of a folder inside one concrete sample."""

    name: str
    is_folder: bool
    asset: Asset | None = None
    leaf: Leaf | None = None
    index: int | None = None

    @property
    def path(self) -> str | None:
        return None if self.asset is None else self.asset.path


def _normalize_field_spec(name: str, spec: Any, *, level: str) -> tuple[str, str]:
    if isinstance(spec, (str, pa.DataType)):
        type_spec, description = spec, ""
    elif isinstance(spec, Sequence) and not isinstance(spec, (bytes, bytearray)) and len(spec) == 2:
        type_spec, description = spec
    else:
        raise ContractError(f"field {level}.{name} must be declared as [type, description], got {spec!r}")
    if not isinstance(description, str):
        raise ContractError(f"description of {level}.{name} must be a string")
    try:
        dtype = parse_type(type_spec)
    except ContractError as exc:
        raise ContractError(f"field {level}.{name}: {exc}") from exc
    return type_name(dtype), description


@dataclass(frozen=True, init=False, eq=False)
class Contract:
    """Immutable ``taco:structure`` plus ``taco:metadata``.

    ``structure`` is a list of sample-relative leaf paths (or ``None`` when
    each sample is a single file). ``metadata`` maps every non-leaf level
    (``collection``, ``sample``, ``sample/<folder>``) to ``{field: [type,
    description]}``. Levels without fields may be omitted.
    """

    structure: tuple[str, ...] | None
    metadata: dict[str, dict[str, tuple[str, str]]]
    levels: tuple[str, ...]
    leaves: tuple[Leaf, ...]
    folders: frozenset[tuple[str, ...]]
    _children: dict[tuple[str, ...], tuple[tuple[str, Any], ...]] = field(repr=False)
    _types: dict[str, dict[str, pa.DataType]] = field(repr=False)

    def __init__(
        self,
        *,
        structure: Iterable[str] | None,
        metadata: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if structure is None:
            declarations: tuple[str, ...] | None = None
            leaves: tuple[Leaf, ...] = ()
        else:
            if isinstance(structure, (str, bytes)):
                raise ContractError("structure must be a list of paths or None")
            declarations = tuple(structure)
            if not declarations:
                raise ContractError("structure must contain at least one leaf, or be None")
            leaves = tuple(self._parse_leaf(item) for item in declarations)

        children = self._build_tree(leaves)
        folders = frozenset(folder for folder in children if folder)
        levels = self._derive_levels(children)
        normalized = self._normalize_metadata(metadata or {}, levels)
        types = {
            level: {name: parse_type(type_spec) for name, (type_spec, _) in fields.items()}
            for level, fields in normalized.items()
        }

        object.__setattr__(self, "structure", declarations)
        object.__setattr__(self, "metadata", normalized)
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "leaves", leaves)
        object.__setattr__(self, "folders", folders)
        object.__setattr__(self, "_children", children)
        object.__setattr__(self, "_types", types)

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _parse_leaf(declaration: str) -> Leaf:
        if not isinstance(declaration, str):
            raise ContractError(f"structure entries must be strings, got {declaration!r}")
        declaration = normalize_relative_path(declaration, context="structure path", allow_glob=True)
        parts = PurePosixPath(declaration).parts
        folder, basename = parts[:-1], parts[-1]
        for component in folder:
            validate_component(component, context="structure folder")

        match = _VARIABLE_LEAF.match(basename)
        if match is None:
            if any(char in basename for char in "*[]"):
                raise ContractError(f"malformed variable leaf {declaration!r}; expected prefix*[min,max].ext")
            validate_component(basename, context="structure file")
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
        return Leaf(declaration, folder, basename, prefix=prefix, minimum=minimum, maximum=maximum, suffix=suffix)

    @staticmethod
    def _build_tree(leaves: tuple[Leaf, ...]) -> dict[tuple[str, ...], tuple[tuple[str, Any], ...]]:
        """Return ordered children per folder: ("folder", name) or ("leaf", Leaf)."""
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
            children.setdefault(leaf.folder, []).append(("leaf", leaf))

        for folder, entries in children.items():
            identifiers: dict[str, str] = {}
            for kind, item in entries:
                identifier = item if kind == "folder" else item.identifier
                if identifier in identifiers:
                    where = "/".join(folder) or "the sample root"
                    raise ContractError(f"sibling identifier {identifier!r} is declared twice under {where}")
                identifiers[identifier] = kind
            Contract._check_ambiguity(folder, [item for kind, item in entries if kind == "leaf"])

        return {folder: tuple(entries) for folder, entries in children.items()}

    @staticmethod
    def _check_ambiguity(folder: tuple[str, ...], leaves: list[Leaf]) -> None:
        """Reject leaf families whose instances could match two declarations."""
        where = "/".join(folder) or "the sample root"
        variables = [leaf for leaf in leaves if leaf.variable]
        for fixed in (leaf for leaf in leaves if not leaf.variable):
            for variable in variables:
                if variable.match_index(fixed.name) is not None:
                    raise ContractError(
                        f"fixed leaf {fixed.declaration!r} matches variable leaf {variable.declaration!r} under {where}"
                    )
        for first in variables:
            for second in variables:
                if first is second or first.suffix != second.suffix:
                    continue
                assert first.prefix is not None and second.prefix is not None
                if second.prefix.startswith(first.prefix) and second.prefix[len(first.prefix) :].isdigit():
                    raise ContractError(
                        f"variable leaves {first.declaration!r} and {second.declaration!r} overlap under {where}"
                    )

    @staticmethod
    def _derive_levels(children: Mapping[tuple[str, ...], Any]) -> tuple[str, ...]:
        folders = [folder for folder in children if folder]
        order = {folder: index for index, folder in enumerate(folders)}
        folders.sort(key=lambda item: (len(item), order[item]))
        if not children or (len(children) == 1 and () in children and not children[()]):
            return (COLLECTION_LEVEL,)
        return (COLLECTION_LEVEL, SAMPLE_LEVEL, *(SAMPLE_LEVEL + "/" + "/".join(folder) for folder in folders))

    @staticmethod
    def _normalize_metadata(
        metadata: Mapping[str, Mapping[str, Any]], levels: tuple[str, ...]
    ) -> dict[str, dict[str, tuple[str, str]]]:
        if not isinstance(metadata, Mapping):
            raise ContractError("metadata must be a mapping keyed by level")
        extra = sorted(set(metadata) - set(levels))
        if extra:
            raise ContractError(
                f"metadata declares levels that do not exist in the structure: {extra}; valid levels are {list(levels)}"
            )
        result: dict[str, dict[str, tuple[str, str]]] = {}
        for level in levels:
            declared = metadata.get(level) or {}
            if not isinstance(declared, Mapping):
                raise ContractError(f"metadata for level {level!r} must be a mapping of fields")
            fields: dict[str, tuple[str, str]] = {}
            for name, spec in declared.items():
                validate_field_name(name, context=level)
                fields[name] = _normalize_field_spec(name, spec, level=level)
            result[level] = fields
        return result

    # ------------------------------------------------------------------ queries

    @property
    def is_null(self) -> bool:
        """True when ``taco:structure`` is ``null`` (one file per sample)."""
        return self.structure is None

    def fields(self, level: str) -> dict[str, tuple[str, str]]:
        return self.metadata[level]

    def arrow_types(self, level: str) -> dict[str, pa.DataType]:
        return self._types[level]

    def arrow_type(self, level: str, name: str) -> pa.DataType:
        return self._types[level][name]

    def children(self, folder: tuple[str, ...]) -> tuple[tuple[str, Any], ...]:
        return self._children[folder]

    def is_folder(self, folder: tuple[str, ...], name: str) -> bool:
        return (*folder, name) in self.folders

    def level_of_folder(self, folder: tuple[str, ...]) -> str:
        return SAMPLE_LEVEL if not folder else SAMPLE_LEVEL + "/" + "/".join(folder)

    def leaf_for(self, folder: tuple[str, ...], name: str) -> tuple[Leaf, int] | None:
        """Return the leaf (and instance index) that a file name instantiates."""
        for kind, item in self._children.get(folder, ()):
            if kind == "leaf":
                index = item.match_index(name)
                if index is not None:
                    return item, index
        return None

    # --------------------------------------------------------------- expansion

    def expand(self, assets: Sequence[Asset]) -> dict[tuple[str, ...], list[Node]]:
        """Resolve assets against the structure and return children per folder."""
        if self.is_null:
            if len(assets) != 1 or assets[0].path is not None:
                raise SampleError(
                    "this contract has no structure (taco:structure = null); "
                    "each sample is exactly one source without a contract path"
                )
            return {(): []}

        by_path: dict[str, Asset] = {}
        for asset in assets:
            if asset.path is None:
                raise SampleError("every asset needs a contract path for this structure")
            if asset.path in by_path:
                raise SampleError(f"duplicate asset path {asset.path!r}")
            by_path[asset.path] = asset

        grouped: dict[tuple[str, ...], dict[str, Asset]] = {}
        for path, asset in by_path.items():
            parts = PurePosixPath(path).parts
            grouped.setdefault(parts[:-1], {})[parts[-1]] = asset

        consumed: set[str] = set()
        tree: dict[tuple[str, ...], list[Node]] = {}
        for folder, entries in self._children.items():
            available = grouped.get(folder, {})
            nodes: list[Node] = []
            for kind, item in entries:
                if kind == "folder":
                    nodes.append(Node(item, True))
                    continue
                leaf: Leaf = item
                matches: list[tuple[int, str, Asset]] = []
                for name, asset in available.items():
                    index = leaf.match_index(name)
                    if index is not None:
                        matches.append((index, name, asset))
                matches.sort()
                location = "/".join(folder) or "the sample root"
                if leaf.variable:
                    indexes = [index for index, _, _ in matches]
                    count = len(matches)
                    if indexes != list(range(count)) or not (leaf.minimum <= count <= leaf.maximum):
                        raise SampleError(
                            f"variable leaf {leaf.declaration!r} under {location} requires "
                            f"contiguous indexes 0..k-1 with {leaf.minimum} <= k <= {leaf.maximum}; "
                            f"got {indexes}"
                        )
                    for index, name, asset in matches:
                        nodes.append(Node(name, False, asset, leaf, index))
                        consumed.add(asset.path)  # type: ignore[arg-type]
                else:
                    if len(matches) != 1:
                        raise SampleError(f"required asset {leaf.declaration!r} is missing")
                    _, name, asset = matches[0]
                    nodes.append(Node(name, False, asset, leaf, 0))
                    consumed.add(asset.path)  # type: ignore[arg-type]
            tree[folder] = nodes

        unexpected = sorted(set(by_path) - consumed)
        if unexpected:
            raise SampleError(f"assets do not match the structure: {unexpected}")
        return tree

    @staticmethod
    def ordered_assets(tree: Mapping[tuple[str, ...], Sequence[Node]]) -> tuple[Asset, ...]:
        """Flatten a tree into assets in structure order (parents first)."""
        ordered: list[Asset] = []
        for folder in tree:
            for node in tree[folder]:
                if node.asset is not None:
                    ordered.append(node.asset)
        return tuple(ordered)

    # -------------------------------------------------------------- validation

    def validate_sample(self, sample: Sample) -> Sample:
        """Return a normalized copy of ``sample`` or raise :class:`SampleError`."""
        if not isinstance(sample, Sample):
            raise SampleError(f"expected a Sample, got {type(sample).__name__}")
        tree = self.expand(sample.assets)

        unknown = sorted(set(sample.metadata) - set(self.levels))
        if unknown:
            raise SampleError(f"sample metadata uses unknown levels {unknown}; valid levels are {list(self.levels)}")

        normalized: dict[str, Any] = {}
        for level in self.levels:
            schema = self.metadata[level]
            provided = sample.metadata.get(level)
            if level == COLLECTION_LEVEL:
                normalized[level] = self._validate_values(level, provided, schema)
                continue

            folder = level_folder(level)
            expected = [node.name for node in tree[folder]]
            if provided is None:
                provided = {}
            if not isinstance(provided, Mapping):
                raise SampleError(f"metadata for {level!r} must be a mapping keyed by child name")
            if schema:
                if list(provided) != expected and set(provided) != set(expected):
                    missing = [name for name in expected if name not in provided]
                    extra = sorted(set(provided) - set(expected))
                    raise SampleError(
                        f"metadata children for {level!r} must be {expected}; missing={missing}, unexpected={extra}"
                    )
            else:
                extra = sorted(set(provided) - set(expected))
                if extra:
                    raise SampleError(f"metadata for {level!r} names unknown children {extra}")
            normalized[level] = {
                name: self._validate_values(level, provided.get(name), schema, child=name) for name in expected
            }

        if self.is_null:
            assets: tuple[Asset, ...] = sample.assets
        else:
            assets = self.ordered_assets(tree)
        return Sample(assets=assets, metadata=normalized)

    def _validate_values(
        self,
        level: str,
        values: Any,
        schema: Mapping[str, tuple[str, str]],
        *,
        child: str | None = None,
    ) -> dict[str, Any]:
        where = f"{level}[{child!r}]" if child is not None else level
        if values is None:
            if schema:
                raise SampleError(f"metadata for {where} is missing; fields {list(schema)} are required")
            return {}
        if not isinstance(values, Mapping):
            raise SampleError(f"metadata for {where} must be a mapping of fields")
        if set(values) != set(schema):
            missing = [name for name in schema if name not in values]
            extra = sorted(set(values) - set(schema))
            raise SampleError(f"fields for {where} must be {list(schema)}; missing={missing}, unexpected={extra}")
        result: dict[str, Any] = {}
        types = self._types[level]
        for name in schema:
            try:
                result[name] = coerce_value(values[name], types[name])
            except (TypeError, ValueError) as exc:
                raise SampleError(f"invalid value for {where}.{name}: {exc}") from exc
        return result

    # ------------------------------------------------------------ serialization

    def to_dict(self) -> dict[str, Any]:
        return {
            "taco:structure": None if self.structure is None else list(self.structure),
            "taco:metadata": {
                level: {name: [type_spec, description] for name, (type_spec, description) in fields.items()}
                for level, fields in self.metadata.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Contract:
        if "taco:structure" not in data or "taco:metadata" not in data:
            raise ContractError("COLLECTION.json must declare taco:structure and taco:metadata")
        return cls(structure=data["taco:structure"], metadata=data["taco:metadata"])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Contract):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash(json.dumps(self.to_dict(), sort_keys=True))

    def describe(self) -> str:
        """Human-readable summary of the structure and every level's fields."""
        lines = ["structure:"]
        if self.structure is None:
            lines.append("  (null: one file per sample)")
        else:
            lines.extend(f"  {item}" for item in self.structure)
        lines.append("metadata:")
        for level in self.levels:
            lines.append(f"  {level}:")
            fields = self.metadata[level]
            if not fields:
                lines.append("    (no fields)")
            for name, (type_spec, description) in fields.items():
                suffix = f"  # {description}" if description else ""
                lines.append(f"    {name}: {type_spec}{suffix}")
        return "\n".join(lines)
