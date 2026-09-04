from __future__ import annotations

import pyarrow as pa
import pytest

from taco.contract.types import coerce_value, parse_type, type_name
from taco.errors import TypeSpecError


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("double", pa.float64()),
        ("float64", pa.float64()),
        ("float", pa.float32()),
        ("float32", pa.float32()),
        ("int32", pa.int32()),
        ("uint64", pa.uint64()),
        ("string", pa.string()),
        ("utf8", pa.string()),
        ("binary", pa.binary()),
        ("bool", pa.bool_()),
        ("boolean", pa.bool_()),
        ("timestamp[us]", pa.timestamp("us")),
        ("timestamp[ms, UTC]", pa.timestamp("ms", tz="UTC")),
        ("timestamp[us, tz=UTC]", pa.timestamp("us", tz="UTC")),
        ("date32", pa.date32()),
        ("date32[day]", pa.date32()),
        ("list<double>", pa.list_(pa.float64())),
        ("list[double]", pa.list_(pa.float64())),
        ("list<item: double>", pa.list_(pa.float64())),
        ("large_list<string>", pa.large_list(pa.string())),
        ("fixed_size_list<int8, 3>", pa.list_(pa.int8(), 3)),
        ("fixed_size_list<item: int8>[3]", pa.list_(pa.int8(), 3)),
        ("struct<a: int32, b: list<string>>", pa.struct([("a", pa.int32()), ("b", pa.list_(pa.string()))])),
        ("map<string, int32>", pa.map_(pa.string(), pa.int32())),
        ("decimal128(10, 2)", pa.decimal128(10, 2)),
        ("duration[ms]", pa.duration("ms")),
        ("  Double  ", pa.float64()),
    ],
)
def test_parse_type(spec: str, expected: pa.DataType) -> None:
    assert parse_type(spec) == expected


def test_parse_type_accepts_datatype_instances() -> None:
    assert parse_type(pa.int16()) == pa.int16()


@pytest.mark.parametrize("spec", ["", "list<>", "struct<a int>", "timestamp[us, UTC, extra]", "nope", 5])
def test_parse_type_rejects_garbage(spec) -> None:
    with pytest.raises(TypeSpecError):
        parse_type(spec)


@pytest.mark.parametrize(
    "dtype",
    [
        pa.float64(),
        pa.int8(),
        pa.string(),
        pa.binary(),
        pa.bool_(),
        pa.timestamp("us"),
        pa.timestamp("ns", tz="America/Lima"),
        pa.date32(),
        pa.list_(pa.float32()),
        pa.large_list(pa.binary()),
        pa.list_(pa.int16(), 4),
        pa.struct([("x", pa.float64()), ("name", pa.string())]),
        pa.map_(pa.string(), pa.list_(pa.int32())),
        pa.decimal128(9, 3),
        pa.decimal256(40, 5),
        pa.binary(16),
        pa.float16(),
        pa.duration("s"),
    ],
)
def test_type_name_round_trips(dtype: pa.DataType) -> None:
    assert parse_type(type_name(dtype)) == dtype


def test_coerce_value_rejects_lossy_conversions() -> None:
    with pytest.raises(TypeError):
        coerce_value(3.5, pa.int32())
    with pytest.raises(TypeError):
        coerce_value(True, pa.int32())
    with pytest.raises(TypeError):
        coerce_value("3", pa.int32())
    with pytest.raises(TypeError):
        coerce_value(b"x", pa.string())
    with pytest.raises(TypeError):
        coerce_value("x", pa.binary())
    with pytest.raises(ValueError):
        coerce_value(300, pa.int8())
    with pytest.raises(TypeError):
        coerce_value("2024-01-01", pa.timestamp("us"))


def test_coerce_value_normalizes() -> None:
    assert coerce_value(3, pa.float64()) == 3.0
    assert coerce_value(3.0, pa.int64()) == 3
    assert coerce_value(None, pa.int64()) is None
    assert coerce_value([1, 2], pa.list_(pa.int32())) == [1, 2]
    assert coerce_value({"a": 1}, pa.struct([("a", pa.int32())])) == {"a": 1}
    assert coerce_value(1_700_000_000_000_000, pa.timestamp("us")).year == 2023
