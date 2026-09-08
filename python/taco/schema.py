from __future__ import annotations

import re
import types
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import pyarrow as pa
from pydantic import BaseModel

from .contract.naming import validate_field_name
from .errors import ContractError, SampleError
from .metadata._base import CollectionSummary, DerivedMetadata

_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_NAMESPACES = frozenset({"cozip", "internal", "taco"})


@dataclass(frozen=True)
class Field:
    type: str
    nullable: bool
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "nullable": self.nullable, "description": self.description}


@dataclass(frozen=True)
class Group:
    namespace: str
    model: type[BaseModel] | None
    optional: bool
    fields: tuple[tuple[str, pa.Field], ...]
    derived: DerivedMetadata | None = None
    summaries: tuple[type[CollectionSummary], ...] = ()


def _optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        without_none = tuple(item for item in args if item is not type(None))
        if len(without_none) == 1 and len(without_none) != len(args):
            return without_none[0], True
    return annotation, False


def _arrow_type(annotation: Any) -> pa.DataType:
    annotation, _ = _optional(annotation)
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        arrow = next((item for item in metadata if isinstance(item, pa.DataType)), None)
        return arrow or _arrow_type(base)

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        values = args
        if not values:
            raise ContractError("Literal metadata fields cannot be empty")
        kinds = {type(value) for value in values}
        if len(kinds) != 1:
            raise ContractError("Literal metadata values must share one type")
        return _arrow_type(next(iter(kinds)))
    if annotation is str:
        return pa.string()
    if annotation is bytes:
        return pa.binary()
    if annotation is bool:
        return pa.bool_()
    if annotation is int:
        return pa.int64()
    if annotation is float:
        return pa.float64()
    if annotation is datetime:
        return pa.timestamp("us")
    if annotation is date:
        return pa.date32()
    if annotation is Decimal:
        return pa.decimal128(38, 9)
    if origin is list:
        return pa.list_(_arrow_type(args[0]))
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return pa.list_(_arrow_type(args[0]))
        if args and len(set(args)) == 1:
            return pa.list_(_arrow_type(args[0]), len(args))
        raise ContractError("tuple metadata fields must contain one repeated type")
    if origin is dict:
        return pa.map_(_arrow_type(args[0]), _arrow_type(args[1]))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return pa.struct(
            pa.field(
                name,
                _pydantic_arrow_type(model_field),
                nullable=_optional(model_field.annotation)[1],
            )
            for name, model_field in annotation.model_fields.items()
        )
    raise ContractError(f"unsupported metadata annotation {annotation!r}; use Annotated[T, pyarrow_type]")


def _pydantic_arrow_type(model_field: Any) -> pa.DataType:
    for item in model_field.metadata:
        if isinstance(item, pa.DataType):
            return item
    return _arrow_type(model_field.annotation)


def _model_fields(namespace: str, model: type[BaseModel], optional: bool) -> tuple[tuple[str, pa.Field], ...]:
    result = []
    for name, model_field in model.model_fields.items():
        annotation = model_field.annotation
        if annotation is None:
            raise ContractError(f"{model.__name__}.{name} has no type annotation")
        _, nullable = _optional(annotation)
        qualified = f"{namespace}:{name}"
        validate_field_name(qualified, context="metadata")
        metadata = None
        if model_field.description:
            metadata = {b"description": model_field.description.encode()}
        dtype = _pydantic_arrow_type(model_field)
        result.append((name, pa.field(qualified, dtype, nullable=optional or nullable, metadata=metadata)))
    if not result:
        raise ContractError(f"metadata model {model.__name__} has no fields")
    return tuple(result)


def _summary_types(
    model: type[BaseModel], fields: tuple[tuple[str, pa.Field], ...]
) -> tuple[type[CollectionSummary], ...]:
    summaries = getattr(model, "__taco_summaries__", ())
    if not isinstance(summaries, tuple):
        raise ContractError(f"{model.__name__} summaries must be a tuple")
    names = {name for name, _ in fields}
    for summary in summaries:
        if not isinstance(summary, type) or not issubclass(summary, CollectionSummary):
            raise ContractError(f"{model.__name__} has an invalid collection summary")
        if not isinstance(summary.field, str) or not summary.field:
            raise ContractError(f"{summary.__name__}.field must be a non-empty string")
        missing = sorted(set(summary.requires) - names)
        if missing:
            raise ContractError(f"{summary.__name__} requires fields missing from {model.__name__}: {missing}")
    return summaries


def _model_binding(namespace: str, value: Any) -> Group:
    annotation, optional = _optional(value)
    if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
        raise ContractError(f"metadata group {namespace!r} must be a Pydantic model, Model | None, or derived group")
    expected_namespace = getattr(annotation, "__taco_namespace__", None)
    if expected_namespace is not None and namespace != expected_namespace:
        raise ContractError(
            f"{annotation.__name__} must use metadata namespace {expected_namespace!r}, got {namespace!r}"
        )
    fields = _model_fields(namespace, annotation, optional)
    return Group(namespace, annotation, optional, fields, summaries=_summary_types(annotation, fields))


def _derived_binding(namespace: str, value: DerivedMetadata) -> Group:
    fields = []
    for field in value.fields:
        qualified = f"{namespace}:{field.name}"
        validate_field_name(qualified, context="derived metadata")
        metadata = field.metadata
        fields.append((field.name, pa.field(qualified, field.type, nullable=field.nullable, metadata=metadata)))
    if not fields:
        raise ContractError(f"derived metadata group {namespace!r} has no fields")
    return Group(namespace, None, False, tuple(fields), value)


def _namespace(value: str) -> str:
    if not _NAMESPACE.fullmatch(value):
        raise ContractError(f"invalid metadata namespace {value!r}")
    if value in _RESERVED_NAMESPACES:
        raise ContractError(f"metadata namespace {value!r} is reserved")
    return value


def validate_qualified_field(value: str) -> None:
    if value.count(":") != 1:
        raise ContractError(f"metadata field {value!r} must be namespace:field")
    namespace, name = value.split(":")
    _namespace(namespace)
    if not name:
        raise ContractError(f"metadata field {value!r} has no field name")
    validate_field_name(value, context="metadata")


@dataclass(frozen=True, init=False)
class Level:
    name: str
    groups: tuple[Group, ...]

    def __init__(self, name: str, **groups: Any) -> None:
        if not isinstance(name, str) or not name:
            raise ContractError("metadata level needs a name")
        bindings = []
        for namespace, value in groups.items():
            _namespace(namespace)
            if isinstance(value, DerivedMetadata):
                bindings.append(_derived_binding(namespace, value))
            else:
                bindings.append(_model_binding(namespace, value))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "groups", tuple(bindings))


@dataclass(frozen=True, init=False)
class MetadataSchema:
    levels: tuple[Level, ...]

    def __init__(self, *levels: Level) -> None:
        if not levels:
            raise ContractError("MetadataSchema needs at least one Level")
        if not all(isinstance(level, Level) for level in levels):
            raise ContractError("MetadataSchema accepts Level objects")
        names = [level.name for level in levels]
        if len(names) != len(set(names)):
            raise ContractError("metadata level names must be unique")
        object.__setattr__(self, "levels", tuple(levels))

    def __iter__(self) -> Iterator[Level]:
        return iter(self.levels)


@dataclass(frozen=True, init=False)
class Metadata:
    groups: dict[str, BaseModel]

    def __init__(self, **groups: BaseModel) -> None:
        for namespace, model in groups.items():
            _namespace(namespace)
            if not isinstance(model, BaseModel):
                raise SampleError(f"metadata group {namespace!r} must be a Pydantic model instance")
        object.__setattr__(self, "groups", dict(groups))

    def __bool__(self) -> bool:
        return bool(self.groups)


@dataclass(frozen=True, init=False)
class CollectionMetadata:
    groups: dict[str, BaseModel]
    _flat: dict[str, Any]

    def __init__(self, **groups: BaseModel) -> None:
        for namespace, model in groups.items():
            _namespace(namespace)
            if not isinstance(model, BaseModel):
                raise TypeError(f"collection metadata group {namespace!r} must be a Pydantic model instance")
            scopes: frozenset[str] = getattr(type(model), "__taco_scopes__", frozenset())
            if scopes and "collection" not in scopes:
                raise TypeError(f"{type(model).__name__} is not collection metadata")
        object.__setattr__(self, "groups", dict(groups))
        object.__setattr__(self, "_flat", {})

    def flatten(self) -> dict[str, Any]:
        result: dict[str, Any] = dict(self._flat)
        for namespace, model in self.groups.items():
            for name, value in model.model_dump(mode="json").items():
                result[f"{namespace}:{name}"] = value
        return result

    @classmethod
    def from_flat(cls, values: Mapping[str, Any]) -> CollectionMetadata:
        for name in values:
            validate_qualified_field(name)
        instance = object.__new__(cls)
        object.__setattr__(instance, "groups", {})
        object.__setattr__(instance, "_flat", dict(values))
        return instance


__all__ = [
    "CollectionMetadata",
    "DerivedMetadata",
    "Field",
    "Level",
    "Metadata",
    "MetadataSchema",
    "validate_qualified_field",
]
