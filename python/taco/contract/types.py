from __future__ import annotations

import re
import struct
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pyarrow as pa

from ..errors import TypeSpecError

__all__ = ["check_type", "coerce_value", "parse_type", "type_name"]

_PRIMITIVES: dict[str, pa.DataType] = {
    "bool": pa.bool_(),
    "int8": pa.int8(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "uint8": pa.uint8(),
    "uint16": pa.uint16(),
    "uint32": pa.uint32(),
    "uint64": pa.uint64(),
    "float16": pa.float16(),
    "float": pa.float32(),
    "double": pa.float64(),
    "string": pa.string(),
    "large_string": pa.large_string(),
    "binary": pa.binary(),
    "large_binary": pa.large_binary(),
    "date32": pa.date32(),
    "date64": pa.date64(),
}
_UNITS = {"s", "ms", "us", "ns"}
_TIMEZONE = re.compile(r"[+-]\d{2}:\d{2}|[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*")
_INTEGER = re.compile(r"-?[0-9]+")
# The type syntax around a struct field name, which the name cannot contain.
_STRUCT_SYNTAX = frozenset(":,<>[]()")

_HEAD = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", re.S)


def _split_top_level(text: str) -> list[str]:
    """Split ``text`` on commas that are not nested inside brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "<[(":
            depth += 1
        elif char in ">])":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _strip_bracket(text: str, opening: str, closing: str) -> str | None:
    text = text.strip()
    if text.startswith(opening) and text.endswith(closing):
        return text[1:-1]
    return None


def _nested_type(spec: str) -> tuple[pa.DataType, bool]:
    text = spec.strip()
    nullable = text.endswith("?")
    if nullable:
        text = text[:-1].rstrip()
    if not text:
        raise TypeSpecError(f"nested type must precede '?' in {spec!r}")
    return parse_type(text), nullable


def _unit(text: str, spec: str) -> str:
    unit = text.strip()
    if unit not in _UNITS:
        raise TypeSpecError(f"unknown time unit {unit!r} in {spec!r}")
    return unit


def _check_struct_name(name: str, spec: str) -> None:
    if not name or name != name.strip() or not _STRUCT_SYNTAX.isdisjoint(name):
        raise TypeSpecError(
            f"struct field name {name!r} in {spec!r} must be non-empty, without surrounding spaces, "
            f"and must not contain any of {''.join(sorted(_STRUCT_SYNTAX))}"
        )


def parse_type(spec: str | pa.DataType) -> pa.DataType:
    """Return the :mod:`pyarrow` type described by the canonical type string ``spec``."""
    if isinstance(spec, pa.DataType):
        return spec
    if not isinstance(spec, str) or not spec.strip():
        raise TypeSpecError(f"type specification must be a non-empty string, got {spec!r}")

    text = spec.strip()
    match = _HEAD.match(text)
    if match is None:
        raise TypeSpecError(f"cannot parse type {spec!r}")
    head = match.group(1)
    rest = match.group(2).strip()

    if not rest:
        primitive = _PRIMITIVES.get(head)
        if primitive is None:
            raise TypeSpecError(f"unsupported type {spec!r}")
        return primitive

    angle = _strip_bracket(rest, "<", ">")
    square = _strip_bracket(rest, "[", "]")
    paren = _strip_bracket(rest, "(", ")")

    try:
        if head == "timestamp" and square is not None:
            args = _split_top_level(square)
            if len(args) not in (1, 2):
                raise TypeSpecError(f"timestamp needs [unit] or [unit, timezone] in {spec!r}")
            unit = _unit(args[0], spec)
            tz = args[1].strip() if len(args) == 2 else None
            if tz is not None and _TIMEZONE.fullmatch(tz) is None:
                raise TypeSpecError(f"unsupported timezone {tz!r} in {spec!r}")
            return pa.timestamp(unit, tz=tz)
        if head == "time32" and square is not None:
            return pa.time32(_unit(square, spec))
        if head == "time64" and square is not None:
            return pa.time64(_unit(square, spec))
        if head == "duration" and square is not None:
            return pa.duration(_unit(square, spec))
        if head in {"list", "large_list"} and angle is not None:
            args = _split_top_level(angle)
            if len(args) != 1:
                raise TypeSpecError(f"{head} takes exactly one type in {spec!r}")
            inner, nullable = _nested_type(args[0])
            field = pa.field("item", inner, nullable=nullable)
            return pa.list_(field) if head == "list" else pa.large_list(field)
        if head == "fixed_size_list" and angle is not None:
            args = _split_top_level(angle)
            if len(args) != 2 or not args[1].isascii() or not args[1].isdigit():
                raise TypeSpecError(f"fixed_size_list needs <type, size> in {spec!r}")
            inner, nullable = _nested_type(args[0])
            return pa.list_(pa.field("item", inner, nullable=nullable), int(args[1]))
        if head == "struct" and angle is not None:
            fields = []
            for item in _split_top_level(angle):
                name, sep, inner = item.partition(":")
                if not sep or not name.strip():
                    raise TypeSpecError(f"struct fields need 'name: type' in {spec!r}")
                _check_struct_name(name.strip(), spec)
                dtype, nullable = _nested_type(inner)
                fields.append(pa.field(name.strip(), dtype, nullable=nullable))
            # Parquet cannot store a struct without fields.
            if not fields:
                raise TypeSpecError(f"struct needs at least one field in {spec!r}")
            return pa.struct(fields)
        if head == "map" and angle is not None:
            args = _split_top_level(angle)
            if len(args) != 2:
                raise TypeSpecError(f"map needs <key, value> in {spec!r}")
            key_type, key_nullable = _nested_type(args[0])
            if key_nullable:
                raise TypeSpecError(f"map keys must not be nullable in {spec!r}")
            item_type, item_nullable = _nested_type(args[1])
            return pa.map_(key_type, pa.field("value", item_type, nullable=item_nullable))
        if head in {"decimal128", "decimal256"} and paren is not None:
            args = _split_top_level(paren)
            if len(args) != 2 or not all(_INTEGER.fullmatch(arg) for arg in args):
                raise TypeSpecError(f"{head} needs (precision, scale) in {spec!r}")
            precision, scale = int(args[0]), int(args[1])
            decimal = pa.decimal256(precision, scale) if head == "decimal256" else pa.decimal128(precision, scale)
            # Arrow allows these scales, but Parquet does not.
            if not 0 <= scale <= precision:
                raise TypeSpecError(f"{head} scale must be between 0 and its precision in {spec!r}")
            return decimal
        if head == "fixed_size_binary" and square is not None:
            # Arrow reads a size of -1 as variable-width binary, and Parquet cannot store 0.
            if _INTEGER.fullmatch(square.strip()) is None or int(square) < 1:
                raise TypeSpecError(f"fixed_size_binary needs a positive size in {spec!r}")
            return pa.binary(int(square))
    except TypeSpecError:
        raise
    except RecursionError as exc:
        raise TypeSpecError("type nests too deeply") from exc
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise TypeSpecError(f"unsupported type {spec!r}: {exc}") from exc

    raise TypeSpecError(f"unsupported type {spec!r}")


def type_name(dtype: pa.DataType) -> str:
    """Return the canonical TACO string for a :mod:`pyarrow` type."""
    if pa.types.is_timestamp(dtype):
        if dtype.tz:
            return f"timestamp[{dtype.unit}, {dtype.tz}]"
        return f"timestamp[{dtype.unit}]"
    if pa.types.is_fixed_size_list(dtype):
        return f"fixed_size_list<{_field_type_name(dtype.value_field)}, {dtype.list_size}>"
    if pa.types.is_large_list(dtype):
        return f"large_list<{_field_type_name(dtype.value_field)}>"
    if pa.types.is_list(dtype):
        return f"list<{_field_type_name(dtype.value_field)}>"
    if pa.types.is_struct(dtype):
        inner = ", ".join(f"{field.name}: {_field_type_name(field)}" for field in dtype)
        return f"struct<{inner}>"
    if pa.types.is_map(dtype):
        return f"map<{type_name(dtype.key_type)}, {_field_type_name(dtype.item_field)}>"
    if pa.types.is_decimal(dtype):
        head = "decimal256" if pa.types.is_decimal256(dtype) else "decimal128"
        return f"{head}({dtype.precision}, {dtype.scale})"
    if pa.types.is_fixed_size_binary(dtype):
        return f"fixed_size_binary[{dtype.byte_width}]"
    if pa.types.is_date32(dtype):
        return "date32"
    if pa.types.is_date64(dtype):
        return "date64"
    if pa.types.is_float16(dtype):
        return "float16"
    return str(dtype)


def _field_type_name(field: pa.Field) -> str:
    suffix = "?" if field.nullable else ""
    return type_name(field.type) + suffix


def _struct_names(dtype: pa.DataType) -> Iterator[str]:
    if pa.types.is_struct(dtype):
        fields = list(dtype)
        yield from (field.name for field in fields)
    elif pa.types.is_map(dtype):
        fields = [dtype.key_field, dtype.item_field]
    elif pa.types.is_list(dtype) or pa.types.is_large_list(dtype) or pa.types.is_fixed_size_list(dtype):
        fields = [dtype.value_field]
    else:
        return
    for field in fields:
        yield from _struct_names(field.type)


def check_type(dtype: pa.DataType) -> None:
    """Reject a :mod:`pyarrow` type that its canonical string does not describe exactly."""
    name = type_name(dtype)
    for struct_name in _struct_names(dtype):
        _check_struct_name(struct_name, name)
    parsed = parse_type(name)
    if parsed != dtype:
        raise TypeSpecError(f"{dtype} has no canonical type string; {name!r} would read back as {parsed}")


def _reject(value: object, dtype: pa.DataType, reason: str) -> None:
    raise TypeError(f"{reason} (expected {type_name(dtype)}, got {type(value).__name__})")


# Microseconds in each unit coarser than a Python microsecond.
_UNIT_MICROSECONDS = {"s": 1_000_000, "ms": 1_000}


def _check_precision(value: object, microseconds: int, dtype: pa.DataType) -> None:
    if microseconds % _UNIT_MICROSECONDS.get(dtype.unit, 1):
        raise ValueError(f"cannot store {value!r} as {type_name(dtype)} without truncating it")


def _check_scalar(value: object, dtype: pa.DataType, *, nullable: bool = True) -> None:
    """Reject Python values that pyarrow would silently truncate or convert."""
    if value is None:
        if not nullable:
            _reject(value, dtype, "null is not allowed")
        return
    if pa.types.is_boolean(dtype):
        if not isinstance(value, bool):
            _reject(value, dtype, "expected a bool")
    elif pa.types.is_integer(dtype):
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                return
            _reject(value, dtype, "expected an integer")
    elif pa.types.is_decimal(dtype):
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            _reject(value, dtype, "expected an int or Decimal")
    elif pa.types.is_floating(dtype):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _reject(value, dtype, "expected a number")
        elif dtype.bit_width in _NARROW_FLOATS:
            # Arrow turns a finite value beyond the range into infinity.
            try:
                _NARROW_FLOATS[dtype.bit_width].pack(float(value))
            except OverflowError as exc:
                raise ValueError(f"cannot store {value!r} as {type_name(dtype)}: it is out of range") from exc
    elif pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        if not isinstance(value, str):
            _reject(value, dtype, "expected a str")
    elif pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype) or pa.types.is_fixed_size_binary(dtype):
        if not isinstance(value, (bytes, bytearray, memoryview)):
            _reject(value, dtype, "expected bytes")
    elif pa.types.is_timestamp(dtype):
        if isinstance(value, bool) or not isinstance(value, (datetime, int)):
            _reject(value, dtype, "expected a datetime or an integer epoch value")
        if isinstance(value, datetime):
            aware = value.utcoffset() is not None
            if aware and dtype.tz is None:
                _reject(value, dtype, "timezone-aware datetime needs a timestamp with a timezone")
            if not aware and dtype.tz is not None:
                _reject(value, dtype, "naive datetime needs a timestamp without a timezone")
            _check_precision(value, value.microsecond, dtype)
    elif pa.types.is_date(dtype):
        if isinstance(value, datetime) or not isinstance(value, date):
            _reject(value, dtype, "expected a date")
    elif pa.types.is_time(dtype):
        if isinstance(value, bool) or not isinstance(value, (time, int)):
            _reject(value, dtype, "expected a time or an integer value")
        if isinstance(value, time):
            _check_precision(value, value.microsecond, dtype)
    elif pa.types.is_duration(dtype):
        if isinstance(value, bool) or not isinstance(value, (timedelta, int)):
            _reject(value, dtype, "expected a timedelta or an integer value")
        if isinstance(value, timedelta):
            _check_precision(value, value.microseconds, dtype)
    elif pa.types.is_list(dtype) or pa.types.is_large_list(dtype) or pa.types.is_fixed_size_list(dtype):
        if not isinstance(value, (list, tuple)):
            _reject(value, dtype, "expected a list")
        else:
            for item in value:
                _check_scalar(item, dtype.value_type, nullable=dtype.value_field.nullable)
    elif pa.types.is_struct(dtype):
        if not isinstance(value, Mapping):
            _reject(value, dtype, "expected a dict")
        else:
            unexpected = set(value) - set(dtype.names)
            if unexpected:
                _reject(value, dtype, f"unexpected struct fields {sorted(unexpected)}")
            for field in dtype:
                _check_scalar(value.get(field.name), field.type, nullable=field.nullable)
    elif pa.types.is_map(dtype):
        if isinstance(value, Mapping):
            items: Sequence[object] = list(value.items())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            items = value
        else:
            _reject(value, dtype, "expected a mapping or key-value pairs")
            return
        for item in items:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)) or len(item) != 2:
                _reject(value, dtype, "expected key-value pairs")
            key, mapped = item
            _check_scalar(key, dtype.key_type, nullable=dtype.key_field.nullable)
            _check_scalar(mapped, dtype.item_type, nullable=dtype.item_field.nullable)


_NOT_SIMPLE = object()
_FLOAT32 = struct.Struct("<f")
_NARROW_FLOATS = {16: struct.Struct("<e"), 32: _FLOAT32}
_INTEGER_BOUNDS = {
    dtype: (0, 2**dtype.bit_width - 1)
    if pa.types.is_unsigned_integer(dtype)
    else (-(2 ** (dtype.bit_width - 1)), 2 ** (dtype.bit_width - 1) - 1)
    for dtype in (pa.int8(), pa.int16(), pa.int32(), pa.int64(), pa.uint8(), pa.uint16(), pa.uint32(), pa.uint64())
}


def _simple_value(value: object, dtype: pa.DataType) -> object:
    """Plain scalars skip the Arrow round trip; anything else returns _NOT_SIMPLE."""
    if type(value) is str and (pa.types.is_string(dtype) or pa.types.is_large_string(dtype)):
        assert isinstance(value, str)
        if value.isascii():
            return value
        try:
            value.encode()
        except UnicodeEncodeError:
            return _NOT_SIMPLE
        return value
    if type(value) is float:
        if pa.types.is_float64(dtype):
            return value
        if pa.types.is_float32(dtype):
            try:
                return _FLOAT32.unpack(_FLOAT32.pack(value))[0]
            except OverflowError:
                return _NOT_SIMPLE
    if type(value) is bool and pa.types.is_boolean(dtype):
        return value
    if type(value) is int and dtype in _INTEGER_BOUNDS:
        assert isinstance(value, int)
        low, high = _INTEGER_BOUNDS[dtype]
        if low <= value <= high:
            return value
    return _NOT_SIMPLE


def coerce_value(value: object, dtype: pa.DataType, *, nullable: bool = True) -> object:
    """Validate ``value`` against ``dtype`` and return its normalized Python form.

    Raises :class:`TypeError` or :class:`ValueError` when the value cannot be
    stored losslessly in a column of type ``dtype``.
    """
    _check_scalar(value, dtype, nullable=nullable)
    if value is None:
        return None
    simple = _simple_value(value, dtype)
    if simple is not _NOT_SIMPLE:
        return simple
    try:
        array = pa.array([value], type=dtype)
    except (pa.ArrowException, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"cannot store {value!r} as {type_name(dtype)}: {exc}") from exc
    return array[0].as_py()
