from __future__ import annotations

import re
from datetime import date, datetime

import pyarrow as pa

from ..errors import TypeSpecError

__all__ = ["coerce_value", "parse_type", "type_name"]

_PRIMITIVE_ALIASES: dict[str, str] = {
    "float32": "float",
    "float64": "double",
    "float16": "halffloat",
    "boolean": "bool",
    "utf8": "string",
    "large_utf8": "large_string",
    "str": "string",
    "bytes": "binary",
    "int": "int64",
    "integer": "int64",
    "long": "int64",
    "datetime": "timestamp[us]",
}

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


def _strip_label(text: str) -> str:
    """Drop an ``item:`` / ``tz=`` style label produced by ``str(DataType)``."""
    text = text.strip()
    if ":" in text and not text.startswith(("struct", "map", "list", "timestamp")):
        label, _, rest = text.partition(":")
        if label.strip().isidentifier():
            return rest.strip()
    return text


def parse_type(spec: str | pa.DataType) -> pa.DataType:
    """Return the :mod:`pyarrow` type described by ``spec``."""
    if isinstance(spec, pa.DataType):
        return spec
    if not isinstance(spec, str) or not spec.strip():
        raise TypeSpecError(f"type specification must be a non-empty string, got {spec!r}")

    text = spec.strip()
    match = _HEAD.match(text)
    if match is None:
        raise TypeSpecError(f"cannot parse type {spec!r}")
    head = match.group(1).lower()
    rest = match.group(2).strip()

    if not rest:
        alias = _PRIMITIVE_ALIASES.get(head, head)
        if alias == "timestamp[us]":
            return pa.timestamp("us")
        try:
            return pa.type_for_alias(alias)
        except (KeyError, ValueError) as exc:
            raise TypeSpecError(f"unsupported type {spec!r}") from exc

    angle = _strip_bracket(rest, "<", ">")
    square = _strip_bracket(rest, "[", "]")
    paren = _strip_bracket(rest, "(", ")")

    try:
        if head == "timestamp" and square is not None:
            args = _split_top_level(square)
            unit = args[0].strip()
            tz = None
            if len(args) > 1:
                tz = args[1].strip()
                if tz.startswith("tz="):
                    tz = tz[3:].strip()
                tz = tz.strip("'\"") or None
            if len(args) > 2:
                raise TypeSpecError(f"too many arguments in {spec!r}")
            return pa.timestamp(unit, tz=tz)
        if head in {"time32", "time64", "duration", "date32", "date64"} and square is not None:
            return pa.type_for_alias(f"{head}[{square.strip()}]")
        if head in {"list", "large_list"} and (angle is not None or square is not None):
            args = _split_top_level(angle if angle is not None else square)  # type: ignore[arg-type]
            if len(args) != 1:
                raise TypeSpecError(f"{head} takes exactly one type in {spec!r}")
            inner = parse_type(_strip_label(args[0]))
            return pa.list_(inner) if head == "list" else pa.large_list(inner)
        if head == "fixed_size_list":
            # fixed_size_list<T, n> or pyarrow's fixed_size_list<item: T>[n]
            size_match = re.fullmatch(r"<(.*)>\s*\[(\d+)\]", rest, re.S)
            if size_match is not None:
                inner = parse_type(_strip_label(size_match.group(1)))
                return pa.list_(inner, int(size_match.group(2)))
            if angle is not None:
                args = _split_top_level(angle)
                if len(args) != 2 or not args[1].strip().isdigit():
                    raise TypeSpecError(f"fixed_size_list needs <type, size> in {spec!r}")
                return pa.list_(parse_type(_strip_label(args[0])), int(args[1]))
        if head == "struct" and angle is not None:
            fields = []
            for item in _split_top_level(angle):
                if not item:
                    continue
                name, sep, inner = item.partition(":")
                if not sep or not name.strip():
                    raise TypeSpecError(f"struct fields need 'name: type' in {spec!r}")
                fields.append(pa.field(name.strip(), parse_type(inner)))
            return pa.struct(fields)
        if head == "map" and angle is not None:
            args = _split_top_level(angle)
            if len(args) != 2:
                raise TypeSpecError(f"map needs <key, value> in {spec!r}")
            return pa.map_(parse_type(_strip_label(args[0])), parse_type(_strip_label(args[1])))
        if head in {"decimal", "decimal128", "decimal256"} and paren is not None:
            args = _split_top_level(paren)
            if len(args) != 2:
                raise TypeSpecError(f"decimal needs (precision, scale) in {spec!r}")
            precision, scale = int(args[0]), int(args[1])
            if head == "decimal256":
                return pa.decimal256(precision, scale)
            return pa.decimal128(precision, scale)
        if head == "fixed_size_binary" and square is not None:
            return pa.binary(int(square))
    except TypeSpecError:
        raise
    except (KeyError, ValueError, TypeError) as exc:
        raise TypeSpecError(f"unsupported type {spec!r}: {exc}") from exc

    raise TypeSpecError(f"unsupported type {spec!r}")


def type_name(dtype: pa.DataType) -> str:
    """Return the canonical TACO string for a :mod:`pyarrow` type."""
    if pa.types.is_timestamp(dtype):
        if dtype.tz:
            return f"timestamp[{dtype.unit}, {dtype.tz}]"
        return f"timestamp[{dtype.unit}]"
    if pa.types.is_fixed_size_list(dtype):
        return f"fixed_size_list<{type_name(dtype.value_type)}, {dtype.list_size}>"
    if pa.types.is_large_list(dtype):
        return f"large_list<{type_name(dtype.value_type)}>"
    if pa.types.is_list(dtype):
        return f"list<{type_name(dtype.value_type)}>"
    if pa.types.is_struct(dtype):
        inner = ", ".join(f"{field.name}: {type_name(field.type)}" for field in dtype)
        return f"struct<{inner}>"
    if pa.types.is_map(dtype):
        return f"map<{type_name(dtype.key_type)}, {type_name(dtype.item_type)}>"
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


def _reject(value: object, dtype: pa.DataType, reason: str) -> None:
    raise TypeError(f"{reason} (expected {type_name(dtype)}, got {type(value).__name__})")


def _check_scalar(value: object, dtype: pa.DataType) -> None:
    """Reject Python values that pyarrow would silently truncate or convert."""
    if value is None:
        return
    if pa.types.is_boolean(dtype):
        if not isinstance(value, bool):
            _reject(value, dtype, "expected a bool")
    elif pa.types.is_integer(dtype):
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                return
            _reject(value, dtype, "expected an integer")
    elif pa.types.is_floating(dtype) or pa.types.is_decimal(dtype):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _reject(value, dtype, "expected a number")
    elif pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        if not isinstance(value, str):
            _reject(value, dtype, "expected a str")
    elif pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype) or pa.types.is_fixed_size_binary(dtype):
        if not isinstance(value, (bytes, bytearray, memoryview)):
            _reject(value, dtype, "expected bytes")
    elif pa.types.is_timestamp(dtype):
        if isinstance(value, bool) or not isinstance(value, (datetime, int)):
            _reject(value, dtype, "expected a datetime or an integer epoch value")
    elif pa.types.is_date(dtype):
        if not isinstance(value, date):
            _reject(value, dtype, "expected a date")
    elif pa.types.is_list(dtype) or pa.types.is_large_list(dtype) or pa.types.is_fixed_size_list(dtype):
        if not isinstance(value, (list, tuple)):
            _reject(value, dtype, "expected a list")
        else:
            for item in value:
                _check_scalar(item, dtype.value_type)
    elif pa.types.is_struct(dtype):
        if not isinstance(value, dict):
            _reject(value, dtype, "expected a dict")
        else:
            for field in dtype:
                _check_scalar(value.get(field.name), field.type)


def coerce_value(value: object, dtype: pa.DataType) -> object:
    """Validate ``value`` against ``dtype`` and return its normalized Python form.

    Raises :class:`TypeError` or :class:`ValueError` when the value cannot be
    stored losslessly in a column of type ``dtype``.
    """
    _check_scalar(value, dtype)
    try:
        array = pa.array([value], type=dtype)
    except (pa.ArrowException, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"cannot store {value!r} as {type_name(dtype)}: {exc}") from exc
    return array[0].as_py()
