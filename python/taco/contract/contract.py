from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

import pyarrow as pa

from ..errors import ContractError, SampleError
from ..schema import Field, Group, Metadata, MetadataSchema, validate_qualified_field
from .naming import level_folder
from .sample import Asset, Folder, Sample, _PreparedAsset, _PreparedNode, _PreparedSample
from .structure import Leaf, Node, build_tree, parse_leaf
from .types import coerce_value, parse_type, type_name

SAMPLE_LEVEL = "sample"
CHILDREN_LEVEL = "children"

_SPATIAL_PROFILE_TYPES = {
    "stac": {
        "crs": "string",
        "tensor_shape": "list<int64>",
        "geotransform": "list<double>",
        "time_start": "timestamp[us, UTC]",
        "centroid": "binary",
    },
    "istac": {
        "crs": "string",
        "geometry": "binary",
        "time_start": "timestamp[us, UTC]",
        "centroid": "binary",
    },
}
_SPATIAL_OPTIONAL_TYPES = {
    "time_end": "timestamp[us, UTC]",
    "time_middle": "timestamp[us, UTC]",
}


def _raw_field(name: str, spec: Any, *, level: str) -> Field:
    if isinstance(spec, Mapping):
        extra = sorted(set(spec) - {"type", "nullable", "description"})
        if extra:
            raise ContractError(f"field {level}.{name} has unknown properties {extra}")
        if "type" not in spec or "nullable" not in spec:
            raise ContractError(f"field {level}.{name} needs type and nullable")
        type_spec = spec["type"]
        nullable = spec["nullable"]
        description = spec.get("description", "")
    elif isinstance(spec, (str, pa.DataType)):
        type_spec, nullable, description = spec, False, ""
    elif isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)) and len(spec) == 2:
        type_spec, description = spec
        nullable = True
    else:
        raise ContractError(f"invalid field declaration for {level}.{name}")
    if not isinstance(nullable, bool):
        raise ContractError(f"nullable of {level}.{name} must be a boolean")
    if not isinstance(description, str):
        raise ContractError(f"description of {level}.{name} must be a string")
    try:
        dtype = parse_type(type_spec)
    except ContractError as exc:
        raise ContractError(f"field {level}.{name}: {exc}") from exc
    return Field(type_name(dtype), nullable, description)


def _configuration(value: Mapping[str, Any], *, namespace: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(json.dumps(dict(value), allow_nan=False)))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"derived group {namespace!r} configuration must be JSON serializable") from exc


def _check_derived_descriptors(
    level: str,
    groups: Mapping[str, Mapping[str, Any]],
    fields: Mapping[str, Field],
) -> None:
    produced = [name for descriptor in groups.values() for name in descriptor["produces"]]
    if len(produced) != len(set(produced)):
        raise ContractError(f"derived metadata at {level!r} produces a field more than once")
    available = set(fields) - set(produced)
    pending = dict(groups)
    while pending:
        ready = [name for name, descriptor in pending.items() if set(descriptor["requires"]).issubset(available)]
        if not ready:
            required = {name for descriptor in pending.values() for name in descriptor["requires"]}
            missing = sorted(required - set(fields))
            if missing:
                raise ContractError(f"derived metadata at {level!r} requires missing fields {missing}")
            raise ContractError(f"derived metadata at {level!r} contains a dependency cycle")
        for name in ready:
            available.update(pending.pop(name)["produces"])


def _same_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (left != left and right != right)
    return bool(left == right)


def _check_row_independent(group: Group, inputs: dict[str, list[Any]], produced: dict[str, list[Any]]) -> None:
    """Recompute the first row alone and compare.

    Derived groups run once per buffered batch, so a computation that looks at
    the other rows silently produces a different answer for every batch size.
    Values that aggregate across samples belong in a collection summary, which
    accumulates until the writer closes.
    """
    assert group.derived is not None
    probe = group.derived.compute({name: values[:1] for name, values in inputs.items()})
    for name, values in produced.items():
        alone = list(probe.get(name, ()))
        if len(alone) != 1 or not _same_value(alone[0], values[0]):
            raise SampleError(
                f"derived group {group.namespace!r} depends on the other rows of its batch; "
                f"it runs once per batch, so a value that aggregates across samples "
                f"belongs in a collection summary"
            )


@dataclass(frozen=True, init=False, eq=False)
class Contract:
    structure: tuple[str, ...] | None
    metadata: dict[str, dict[str, Field]]
    derived: dict[str, dict[str, dict[str, Any]]]
    levels: tuple[str, ...]
    leaves: tuple[Leaf, ...]
    folders: frozenset[tuple[str, ...]]
    _children: dict[tuple[str, ...], tuple[tuple[str, Any], ...]] = field(repr=False)
    _types: dict[str, dict[str, pa.DataType]] = field(repr=False)
    _groups: dict[str, tuple[Group, ...]] = field(repr=False)

    def __init__(
        self,
        *,
        structure: Iterable[str] | None,
        metadata: MetadataSchema | Mapping[str, Mapping[str, Any]] | None = None,
        derived: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    ) -> None:
        if structure is None:
            declarations = None
            leaves: tuple[Leaf, ...] = ()
        else:
            if isinstance(structure, (str, bytes)):
                raise ContractError("structure must be a list of paths or None")
            declarations = tuple(structure)
            if not declarations:
                raise ContractError("structure must contain at least one leaf, or be None")
            leaves = tuple(parse_leaf(item) for item in declarations)

        children = build_tree(leaves)
        folders = frozenset(folder for folder in children if folder)
        levels = self._derive_levels(children, structure is None)
        if isinstance(metadata, MetadataSchema):
            normalized, types_, groups, derived_ = self._from_models(metadata, levels, children)
        else:
            normalized = self._from_mapping(metadata or {}, levels)
            types_ = {
                level: {name: parse_type(spec.type) for name, spec in fields.items()}
                for level, fields in normalized.items()
            }
            groups = dict.fromkeys(levels, ())
            derived_ = self._normalize_derived(derived or {}, levels, normalized)
        self._check_spatial_groups(normalized)

        object.__setattr__(self, "structure", declarations)
        object.__setattr__(self, "metadata", normalized)
        object.__setattr__(self, "derived", derived_)
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "leaves", leaves)
        object.__setattr__(self, "folders", folders)
        object.__setattr__(self, "_children", children)
        object.__setattr__(self, "_types", types_)
        object.__setattr__(self, "_groups", groups)

    @staticmethod
    def _check_spatial_groups(metadata: Mapping[str, Mapping[str, Field]]) -> None:
        for level, fields in metadata.items():
            namespaces = {name.partition(":")[0] for name in fields}
            if {"stac", "istac"}.issubset(namespaces):
                raise ContractError(f"metadata level {level!r} must choose either STAC or ISTAC, not both")
            if "stac:geometry" in fields:
                raise ContractError(
                    f"metadata level {level!r} puts geometry in STAC; use the ISTAC group for irregular footprints"
                )
            irregular_grid_fields = {"istac:tensor_shape", "istac:geotransform"}.intersection(fields)
            if irregular_grid_fields:
                raise ContractError(
                    f"metadata level {level!r} puts affine-grid fields in ISTAC; use the STAC group for regular chunks"
                )
            for namespace, expected in _SPATIAL_PROFILE_TYPES.items():
                present = {name.partition(":")[2] for name in fields if name.startswith(f"{namespace}:")}
                if not present:
                    continue
                missing = sorted(set(expected) - present)
                if missing:
                    raise ContractError(f"{namespace.upper()} metadata at level {level!r} is missing fields {missing}")
                for name, expected_type in {**expected, **_SPATIAL_OPTIONAL_TYPES}.items():
                    qualified = f"{namespace}:{name}"
                    if qualified in fields and fields[qualified].type != expected_type:
                        actual = fields[qualified].type
                        raise ContractError(f"field {level}.{qualified} must have type {expected_type}, got {actual}")

    @staticmethod
    def _derive_levels(children: Mapping[tuple[str, ...], Any], null_structure: bool) -> tuple[str, ...]:
        if null_structure:
            return (SAMPLE_LEVEL,)
        folders = [folder for folder in children if folder]
        order = {folder: index for index, folder in enumerate(folders)}
        folders.sort(key=lambda item: (len(item), order[item]))
        return (SAMPLE_LEVEL, CHILDREN_LEVEL, *(CHILDREN_LEVEL + "/" + "/".join(folder) for folder in folders))

    @staticmethod
    def _from_mapping(
        metadata: Mapping[str, Mapping[str, Any]], levels: tuple[str, ...]
    ) -> dict[str, dict[str, Field]]:
        if not isinstance(metadata, Mapping):
            raise ContractError("metadata must be a mapping or MetadataSchema")
        extra = sorted(set(metadata) - set(levels))
        if extra:
            raise ContractError(f"metadata has unknown levels {extra}; valid levels are {list(levels)}")
        result = {}
        for level in levels:
            values = metadata.get(level, {})
            if not isinstance(values, Mapping):
                raise ContractError(f"metadata for {level!r} must be an object")
            fields = {}
            for name, spec in values.items():
                validate_qualified_field(name)
                fields[name] = _raw_field(name, spec, level=level)
            result[level] = fields
        return result

    @classmethod
    def _from_models(
        cls,
        schema: MetadataSchema,
        levels: tuple[str, ...],
        children: Mapping[tuple[str, ...], tuple[tuple[str, Any], ...]],
    ) -> tuple[
        dict[str, dict[str, Field]],
        dict[str, dict[str, pa.DataType]],
        dict[str, tuple[Group, ...]],
        dict[str, dict[str, dict[str, Any]]],
    ]:
        declared = {level.name: level for level in schema}
        extra = sorted(set(declared) - set(levels))
        if extra:
            raise ContractError(f"metadata has unknown levels {extra}; valid levels are {list(levels)}")
        metadata: dict[str, dict[str, Field]] = {}
        types_: dict[str, dict[str, pa.DataType]] = {}
        groups: dict[str, tuple[Group, ...]] = {}
        derived: dict[str, dict[str, dict[str, Any]]] = {}
        for level in levels:
            bindings = declared[level].groups if level in declared else ()
            cls._check_scopes(level, bindings, children)
            level_fields: dict[str, Field] = {}
            level_types: dict[str, pa.DataType] = {}
            level_derived: dict[str, dict[str, Any]] = {}
            for group in bindings:
                for _, arrow_field in group.fields:
                    if arrow_field.name in level_fields:
                        raise ContractError(f"metadata field {arrow_field.name!r} is declared twice at {level}")
                    description = ""
                    if arrow_field.metadata and b"description" in arrow_field.metadata:
                        description = arrow_field.metadata[b"description"].decode()
                    level_fields[arrow_field.name] = Field(
                        type_name(arrow_field.type), arrow_field.nullable, description
                    )
                    level_types[arrow_field.name] = arrow_field.type
                if group.derived is not None:
                    for required in group.derived.requires:
                        validate_qualified_field(required)
                    level_derived[group.namespace] = {
                        "requires": list(group.derived.requires),
                        "produces": [field.name for _, field in group.fields],
                        "configuration": _configuration(group.derived.configuration(), namespace=group.namespace),
                    }
            cls._order_derived(level, bindings, level_fields)
            metadata[level] = level_fields
            types_[level] = level_types
            groups[level] = bindings
            if level_derived:
                derived[level] = level_derived
        return metadata, types_, groups, derived

    @staticmethod
    def _check_scopes(
        level: str,
        groups: Sequence[Group],
        children: Mapping[tuple[str, ...], tuple[tuple[str, Any], ...]],
    ) -> None:
        if level == SAMPLE_LEVEL:
            possible = {"sample"}
        else:
            folder = level_folder(level)
            possible = {"folder" if kind == "folder" else "asset" for kind, _ in children[folder]}
        for group in groups:
            target = group.model if group.model is not None else type(group.derived)
            scopes: frozenset[str] = getattr(target, "__taco_scopes__", frozenset())
            valid = possible.issubset(scopes) if not group.optional else bool(scopes.intersection(possible))
            if scopes and not valid:
                raise ContractError(f"{target.__name__} cannot be used at metadata level {level!r}")

    @staticmethod
    def _order_derived(level: str, groups: Sequence[Group], fields: Mapping[str, Field]) -> None:
        available = {
            field for group in groups if group.derived is None for _, item in group.fields for field in [item.name]
        }
        pending = [group for group in groups if group.derived is not None]
        while pending:
            ready = [
                group
                for group in pending
                if group.derived is not None and set(group.derived.requires).issubset(available)
            ]
            if not ready:
                missing = sorted(
                    {
                        required
                        for group in pending
                        if group.derived is not None
                        for required in group.derived.requires
                        if required not in fields
                    }
                )
                if missing:
                    raise ContractError(f"derived metadata at {level!r} requires missing fields {missing}")
                raise ContractError(f"derived metadata at {level!r} contains a dependency cycle")
            for group in ready:
                available.update(field.name for _, field in group.fields)
                pending.remove(group)

    @staticmethod
    def _normalize_derived(
        derived: Mapping[str, Mapping[str, Mapping[str, Any]]],
        levels: tuple[str, ...],
        metadata: Mapping[str, Mapping[str, Field]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        if not isinstance(derived, Mapping):
            raise ContractError("taco:derived must be an object")
        extra = sorted(set(derived) - set(levels))
        if extra:
            raise ContractError(f"derived metadata has unknown levels {extra}")
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for level, groups in derived.items():
            if not isinstance(groups, Mapping):
                raise ContractError(f"derived metadata for {level!r} must be an object")
            result[level] = {}
            for namespace, descriptor in groups.items():
                validate_qualified_field(f"{namespace}:value")
                if not isinstance(descriptor, Mapping):
                    raise ContractError(f"derived group {namespace!r} must be an object")
                extra = sorted(set(descriptor) - {"requires", "produces", "configuration"})
                if extra:
                    raise ContractError(f"derived group {namespace!r} has unknown properties {extra}")
                requires = descriptor.get("requires")
                produces = descriptor.get("produces")
                configuration = descriptor.get("configuration", {})
                if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
                    raise ContractError(f"derived group {namespace!r} needs a requires list")
                if not isinstance(produces, list) or not all(isinstance(item, str) for item in produces):
                    raise ContractError(f"derived group {namespace!r} needs a produces list")
                if not produces:
                    raise ContractError(f"derived group {namespace!r} must produce at least one field")
                for name in [*requires, *produces]:
                    validate_qualified_field(name)
                if any(not name.startswith(f"{namespace}:") for name in produces):
                    raise ContractError(f"derived group {namespace!r} must produce fields in its own namespace")
                if not set(produces).issubset(metadata[level]):
                    raise ContractError(f"derived group {namespace!r} produces fields absent from taco:metadata")
                if not isinstance(configuration, Mapping):
                    raise ContractError(f"derived group {namespace!r} configuration must be an object")
                result[level][namespace] = {
                    "requires": list(requires),
                    "produces": list(produces),
                    "configuration": _configuration(configuration, namespace=namespace),
                }
            _check_derived_descriptors(level, result[level], metadata[level])
        return result

    @property
    def is_null(self) -> bool:
        return self.structure is None

    def arrow_types(self, level: str) -> dict[str, pa.DataType]:
        return self._types[level]

    def children(self, folder: tuple[str, ...]) -> tuple[tuple[str, Any], ...]:
        return self._children[folder]

    def is_folder(self, folder: tuple[str, ...], name: str) -> bool:
        return (*folder, name) in self.folders

    def level_of_folder(self, folder: tuple[str, ...]) -> str:
        return CHILDREN_LEVEL if not folder else CHILDREN_LEVEL + "/" + "/".join(folder)

    def _resolve_assets(self, assets: Sequence[Asset]) -> tuple[Asset, ...]:
        if self.is_null:
            if len(assets) != 1 or assets[0].path is not None:
                raise SampleError("a contract without structure accepts one asset without a path")
            return tuple(assets)
        resolved = []
        for asset in assets:
            if asset.path is not None:
                resolved.append(asset)
                continue
            if not isinstance(asset.source, Path):
                raise SampleError("inline assets need an explicit contract path")
            matches = [
                leaf for leaf in self.leaves if not leaf.folder and leaf.match_index(asset.source.name) is not None
            ]
            if len(matches) != 1:
                raise SampleError(f"cannot infer a unique contract path from {asset.source.name!r}; pass path=")
            resolved.append(asset.replace(path=asset.source.name))
        return tuple(resolved)

    def expand(self, assets: Sequence[Asset], folders: Sequence[Folder] = ()) -> dict[tuple[str, ...], list[Node]]:
        assets = self._resolve_assets(assets)
        if self.is_null:
            return {(): []}
        folder_metadata = {PurePosixPath(item.path).parts: item.metadata for item in folders}
        unknown_folders = sorted("/".join(path) for path in set(folder_metadata) - set(self.folders))
        if unknown_folders:
            raise SampleError(f"sample names folders absent from the contract: {unknown_folders}")
        by_folder: dict[tuple[str, ...], dict[str, Asset]] = {}
        for asset in assets:
            assert asset.path is not None
            parts = PurePosixPath(asset.path).parts
            bucket = by_folder.setdefault(parts[:-1], {})
            if parts[-1] in bucket:
                raise SampleError(f"duplicate asset path {asset.path!r}")
            bucket[parts[-1]] = asset

        consumed: set[str] = set()
        tree = {}
        for folder, entries in self._children.items():
            available = by_folder.get(folder, {})
            nodes = []
            for kind, item in entries:
                if kind == "folder":
                    path = (*folder, item)
                    nodes.append(Node(item, True, folder_metadata.get(path, Metadata())))
                    continue
                leaf: Leaf = item
                matches = sorted(
                    (index, name, asset)
                    for name, asset in available.items()
                    if (index := leaf.match_index(name)) is not None
                )
                if leaf.variable:
                    indexes = [index for index, _, _ in matches]
                    if indexes != list(range(len(indexes))) or not leaf.minimum <= len(indexes) <= leaf.maximum:
                        raise SampleError(
                            f"{leaf.declaration!r} requires contiguous indexes with "
                            f"{leaf.minimum} <= count <= {leaf.maximum}; got {indexes}"
                        )
                elif len(matches) != 1:
                    raise SampleError(f"required asset {leaf.declaration!r} is missing")
                for index, name, asset in matches:
                    nodes.append(Node(name, False, asset.metadata, asset, leaf, index))
                    assert asset.path is not None
                    consumed.add(asset.path)
            tree[folder] = nodes
        unexpected = sorted(asset.path for asset in assets if asset.path is not None and asset.path not in consumed)
        if unexpected:
            raise SampleError(f"assets do not match the structure: {unexpected}")
        unused_folders = sorted("/".join(path) for path in set(folder_metadata) if not folder_metadata[path])
        if unused_folders:
            raise SampleError(f"empty folder metadata is unnecessary: {unused_folders}")
        return tree

    def validate_sample(self, sample: Sample) -> Sample:
        if not isinstance(sample, Sample):
            raise SampleError(f"expected a Sample, got {type(sample).__name__}")
        assets = self._resolve_assets(sample.assets)
        tree = self.expand(assets, sample.folders)
        self.flatten_metadata(SAMPLE_LEVEL, sample.metadata, scope="sample")
        if not self.is_null:
            for folder, nodes in tree.items():
                level = self.level_of_folder(folder)
                for node in nodes:
                    self.flatten_metadata(level, node.metadata, scope="folder" if node.is_folder else "asset")
        ordered = tuple(node.asset for nodes in tree.values() for node in nodes if node.asset is not None)
        return Sample(assets=assets if self.is_null else ordered, metadata=sample.metadata, folders=sample.folders)

    def prepare_sample(self, sample: Sample) -> _PreparedSample:
        sample = self.validate_sample(sample)
        rows: dict[str, tuple[_PreparedNode, ...]] = {}
        if not self.is_null:
            tree = self.expand(sample.assets, sample.folders)
            for folder, nodes in tree.items():
                level = self.level_of_folder(folder)
                rows[level] = tuple(
                    _PreparedNode(
                        node.name,
                        node.is_folder,
                        self.flatten_metadata(
                            level,
                            node.metadata,
                            scope="folder" if node.is_folder else "asset",
                        ),
                    )
                    for node in nodes
                )
        return _PreparedSample(
            tuple(_PreparedAsset(asset.source, asset.path) for asset in sample.assets),
            self.flatten_metadata(SAMPLE_LEVEL, sample.metadata, scope="sample"),
            rows,
        )

    def flatten_metadata(self, level: str, metadata: Metadata, *, scope: str) -> dict[str, Any]:
        groups = self._groups[level]
        if not groups and metadata.groups:
            raise SampleError(f"metadata is not declared for {level!r}")
        expected = {group.namespace: group for group in groups if group.derived is None}
        derived = {group.namespace for group in groups if group.derived is not None}
        unexpected = sorted(set(metadata.groups) - set(expected))
        if unexpected:
            if set(unexpected) & derived:
                raise SampleError(f"derived metadata groups cannot be supplied: {sorted(set(unexpected) & derived)}")
            raise SampleError(f"metadata groups at {level!r} are not declared: {unexpected}")
        result: dict[str, Any] = {}
        for namespace, group in expected.items():
            model = metadata.groups.get(namespace)
            if model is None:
                if not group.optional:
                    raise SampleError(f"metadata group {namespace!r} is required at {level!r}")
                result.update((field.name, None) for _, field in group.fields)
                continue
            assert group.model is not None
            if not isinstance(model, group.model):
                raise SampleError(
                    f"metadata group {namespace!r} at {level!r} must be {group.model.__name__}, "
                    f"got {type(model).__name__}"
                )
            scopes: frozenset[str] = getattr(type(model), "__taco_scopes__", frozenset())
            if scopes and scope not in scopes:
                raise SampleError(f"{type(model).__name__} cannot describe a {scope}")
            dumped = model.model_dump(mode="python")
            try:
                values = {name: dumped[name] for name, _ in group.fields}
            except KeyError as exc:
                raise SampleError(f"{type(model).__name__} no longer matches the contract") from exc
            for name, arrow_field in group.fields:
                try:
                    result[arrow_field.name] = coerce_value(
                        values[name], arrow_field.type, nullable=arrow_field.nullable
                    )
                except (TypeError, ValueError) as exc:
                    raise SampleError(f"invalid {arrow_field.name} at {level!r}: {exc}") from exc
        return result

    def apply_derived(self, level: str, rows: list[dict[str, Any]], *, verify: bool = False) -> None:
        if not rows:
            return
        pending = [group for group in self._groups[level] if group.derived is not None]
        while pending:
            complete = [
                group
                for group in pending
                if all({field.name for _, field in group.fields}.issubset(row) for row in rows)
            ]
            for group in complete:
                pending.remove(group)
            if not pending:
                return
            available = set(rows[0])
            ready = [
                group
                for group in pending
                if group.derived is not None and set(group.derived.requires).issubset(available)
            ]
            if not ready:
                raise RuntimeError(f"cannot resolve derived metadata at {level!r}")
            for group in ready:
                assert group.derived is not None
                inputs = {name: [row.get(name) for row in rows] for name in group.derived.requires}
                output = group.derived.compute(inputs)
                expected = {name for name, _ in group.fields}
                if set(output) != expected:
                    raise SampleError(
                        f"derived group {group.namespace!r} returned {sorted(output)}, expected {sorted(expected)}"
                    )
                produced: dict[str, list[Any]] = {}
                for name, arrow_field in group.fields:
                    values = list(output[name])
                    if len(values) != len(rows):
                        raise SampleError(f"derived field {arrow_field.name!r} returned the wrong number of rows")
                    produced[name] = values
                if verify and len(rows) > 1:
                    _check_row_independent(group, inputs, produced)
                for name, arrow_field in group.fields:
                    for row, value in zip(rows, produced[name], strict=True):
                        row[arrow_field.name] = coerce_value(value, arrow_field.type, nullable=arrow_field.nullable)
                pending.remove(group)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "taco:structure": None if self.structure is None else list(self.structure),
            "taco:metadata": {
                level: {name: spec.to_dict() for name, spec in fields.items()}
                for level, fields in self.metadata.items()
            },
        }
        if self.derived:
            result["taco:derived"] = self.derived
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Contract:
        if "taco:structure" not in data or "taco:metadata" not in data:
            raise ContractError("COLLECTION.json must declare taco:structure and taco:metadata")
        metadata = data["taco:metadata"]
        if not isinstance(metadata, Mapping):
            raise ContractError("taco:metadata must be an object")
        for level, fields in metadata.items():
            if not isinstance(fields, Mapping):
                raise ContractError(f"metadata for {level!r} must be an object")
            for name, declaration in fields.items():
                if not isinstance(declaration, Mapping) or set(declaration) != {
                    "type",
                    "nullable",
                    "description",
                }:
                    raise ContractError(f"serialized field {level}.{name} must declare type, nullable, and description")
        contract = cls(
            structure=data["taco:structure"],
            metadata=metadata,
            derived=data.get("taco:derived"),
        )
        missing = sorted(set(contract.levels) - set(metadata))
        if missing:
            raise ContractError(f"taco:metadata is missing levels {missing}")
        return contract

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Contract) and self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash(json.dumps(self.to_dict(), sort_keys=True))


__all__ = ["CHILDREN_LEVEL", "SAMPLE_LEVEL", "Contract", "Field", "Leaf", "Node"]
