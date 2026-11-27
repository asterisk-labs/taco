from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pyarrow as pa
import pytest

from taco.contract.types import check_type, coerce_value, parse_type, type_name
from taco.errors import TypeSpecError


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("double", pa.float64()),
        ("float", pa.float32()),
        ("int32", pa.int32()),
        ("uint64", pa.uint64()),
        ("string", pa.string()),
        ("binary", pa.binary()),
        ("bool", pa.bool_()),
        ("timestamp[us]", pa.timestamp("us")),
        ("timestamp[ms, UTC]", pa.timestamp("ms", tz="UTC")),
        ("date32", pa.date32()),
        ("list<double>", pa.list_(pa.field("item", pa.float64(), nullable=False))),
        ("large_list<string>", pa.large_list(pa.field("item", pa.string(), nullable=False))),
        ("fixed_size_list<int8, 3>", pa.list_(pa.field("item", pa.int8(), nullable=False), 3)),
        (
            "struct<a: int32, b: list<string>>",
            pa.struct(
                [
                    pa.field("a", pa.int32(), nullable=False),
                    pa.field("b", pa.list_(pa.field("item", pa.string(), nullable=False)), nullable=False),
                ]
            ),
        ),
        (
            "map<string, int32>",
            pa.map_(pa.string(), pa.field("value", pa.int32(), nullable=False)),
        ),
        ("list<int64?>", pa.list_(pa.field("item", pa.int64(), nullable=True))),
        (
            "list<struct<minimum: double?, valid_count: uint64>>",
            pa.list_(
                pa.field(
                    "item",
                    pa.struct(
                        [
                            pa.field("minimum", pa.float64(), nullable=True),
                            pa.field("valid_count", pa.uint64(), nullable=False),
                        ]
                    ),
                    nullable=False,
                )
            ),
        ),
        ("decimal128(10, 2)", pa.decimal128(10, 2)),
        ("duration[ms]", pa.duration("ms")),
        ("  double  ", pa.float64()),
        ("float16", pa.float16()),
        ("time32[ms]", pa.time32("ms")),
        ("timestamp[ns, +05:30]", pa.timestamp("ns", tz="+05:30")),
        ("decimal256(40, 5)", pa.decimal256(40, 5)),
    ],
)
def test_parse_type(spec: str, expected: pa.DataType) -> None:
    assert parse_type(spec) == expected


def test_parse_type_accepts_datatype_instances() -> None:
    assert parse_type(pa.int16()) == pa.int16()


@pytest.mark.parametrize(
    "spec",
    [
        "float32",
        "float64",
        "halffloat",
        "boolean",
        "utf8",
        "large_utf8",
        "str",
        "bytes",
        "int",
        "integer",
        "long",
        "datetime",
        "timestamp",
        "Double",
        "null",
        "date32[day]",
        "list[double]",
        "list<item: double>",
        "fixed_size_list<item: int8>[3]",
        "timestamp[us, tz=UTC]",
        "timestamp[minutes]",
        "decimal(10, 2)",
    ],
)
def test_parse_type_accepts_only_canonical_names(spec: str) -> None:
    with pytest.raises(TypeSpecError, match=r"unsupported|unknown time unit"):
        parse_type(spec)


@pytest.mark.parametrize(
    "spec",
    ["", "list<>", "struct<a int>", "timestamp[us, UTC, extra]", "map<string?, int64>", "int64?", "nope", 5],
)
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
    check_type(dtype)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("struct<>", "struct needs at least one field"),
        ("struct< >", "struct needs at least one field"),
        ("decimal128(5, 10)", "scale must be between 0 and its precision"),
        ("decimal128(10, -2)", "scale must be between 0 and its precision"),
        ("decimal256(40, 41)", "scale must be between 0 and its precision"),
        ("fixed_size_binary[0]", "needs a positive size"),
        ("fixed_size_binary[-1]", "needs a positive size"),
        ("fixed_size_binary[-5]", "needs a positive size"),
        ("list<struct<>>", "struct needs at least one field"),
    ],
)
def test_parse_type_rejects_types_parquet_cannot_store(spec: str, message: str) -> None:
    with pytest.raises(TypeSpecError, match=message):
        parse_type(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "timestamp[]",
        "fixed_size_list<int64, 100000000000000000000>",
        "fixed_size_list<int64, 2147483648>",
        "fixed_size_list<int64, \u0661>",
        "decimal128(100000000000000000000, 0)",
        "decimal128(10, 100000000000000000000)",
        "decimal128(1_0, 2)",
        "decimal128(+10, 2)",
        "fixed_size_binary[100000000000000000000]",
        "fixed_size_binary[1_6]",
        "list<" * 2000 + "int64" + ">" * 2000,
    ],
)
def test_parse_type_raises_type_errors_for_bad_arguments(spec: str) -> None:
    with pytest.raises(TypeSpecError):
        parse_type(spec)


def test_decimal_scale_may_reach_the_precision() -> None:
    assert parse_type("decimal128(38, 38)") == pa.decimal128(38, 38)
    assert parse_type("decimal128(5, 0)") == pa.decimal128(5, 0)
    assert parse_type("fixed_size_binary[1]") == pa.binary(1)


@pytest.mark.parametrize("name", ["a<b>", "a(1)", "a[0]", "a>b"])
def test_struct_field_names_carry_no_type_syntax(name: str) -> None:
    with pytest.raises(TypeSpecError, match="struct field name"):
        parse_type(f"struct<{name}: int64>")


def test_struct_field_names_may_hold_spaces_inside() -> None:
    assert parse_type("struct<my field: int64,  b : int8>").names == ["my field", "b"]


@pytest.mark.parametrize(
    ("dtype", "message"),
    [
        (pa.struct([pa.field("a:b", pa.int64())]), "struct field name 'a:b'"),
        (pa.struct([pa.field(" a", pa.int64())]), "struct field name ' a'"),
        (pa.struct([pa.field("a ", pa.int64())]), "struct field name 'a '"),
        (pa.struct([pa.field("", pa.int64())]), "struct field name ''"),
        (pa.list_(pa.struct([pa.field("a,b", pa.int64())])), "struct field name 'a,b'"),
        (pa.map_(pa.string(), pa.struct([pa.field("x>y", pa.int64())])), "struct field name 'x>y'"),
        (pa.struct([]), "struct needs at least one field"),
        (pa.string_view(), "unsupported type 'string_view'"),
        (pa.dictionary(pa.int32(), pa.string()), "unsupported type"),
        (pa.null(), "unsupported type 'null'"),
        (pa.decimal32(5, 2), r"decimal32\(5, 2\) has no canonical type string"),
        (pa.decimal128(5, 10), "scale must be between 0 and its precision"),
        (pa.binary(0), "needs a positive size"),
        (pa.timestamp("us", tz="+0530"), "unsupported timezone"),
    ],
)
def test_check_type_rejects_types_without_a_canonical_string(dtype: pa.DataType, message: str) -> None:
    with pytest.raises(TypeSpecError, match=message):
        check_type(dtype)


def test_check_type_ignores_names_the_string_does_not_store() -> None:
    check_type(pa.list_(pa.field("element", pa.int64())))
    check_type(pa.map_(pa.field("k", pa.string(), nullable=False), pa.field("v", pa.int64())))
    check_type(pa.struct([pa.field("my field", pa.list_(pa.struct([pa.field("a?", pa.int8())])))]))


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
    with pytest.raises(ValueError, match="cannot store"):
        coerce_value(300, pa.int8())
    with pytest.raises(TypeError):
        coerce_value("2024-01-01", pa.timestamp("us"))


def test_coerce_value_normalizes() -> None:
    assert coerce_value(3, pa.float64()) == 3.0
    assert coerce_value(3.0, pa.int64()) == 3
    assert coerce_value(None, pa.int64()) is None
    with pytest.raises(TypeError, match="null is not allowed"):
        coerce_value(None, pa.int64(), nullable=False)
    assert coerce_value([1, 2], pa.list_(pa.int32())) == [1, 2]
    assert coerce_value({"a": 1}, pa.struct([("a", pa.int32())])) == {"a": 1}
    with pytest.raises(TypeError, match="null is not allowed"):
        coerce_value({}, pa.struct([pa.field("a", pa.int32(), nullable=False)]))
    assert coerce_value(1_700_000_000_000_000, pa.timestamp("us")).year == 2023
    assert coerce_value(Decimal("1.25"), pa.decimal128(4, 2)) == Decimal("1.25")


@pytest.mark.parametrize(
    ("value", "dtype"),
    [
        (0.1, pa.float32()),
        (-0.0, pa.float32()),
        (1e-46, pa.float32()),
        (3.4028234663852886e38, pa.float32()),
        (float("inf"), pa.float32()),
        ("ñandú", pa.string()),
        ("\ud800", pa.string()),
        (2**64 - 1, pa.uint64()),
        (2**63, pa.int64()),
        (-1, pa.uint8()),
    ],
)
def test_coerce_value_matches_arrow_for_simple_scalars(value: object, dtype: pa.DataType) -> None:
    # The fast path must agree with Arrow, errors included.
    try:
        expected = pa.array([value], type=dtype)[0].as_py()
    except (pa.ArrowException, ValueError, OverflowError, UnicodeEncodeError):
        with pytest.raises(ValueError, match="cannot store"):
            coerce_value(value, dtype)
        return
    assert repr(coerce_value(value, dtype)) == repr(expected)


@pytest.mark.parametrize(
    ("value", "dtype", "match"),
    [
        ({"a": 1.5}, pa.struct([("a", pa.int32())]), "integer"),
        ({"a": 1, "extra": 2}, pa.struct([("a", pa.int32())]), "unexpected struct"),
        ({"a": 1.5}, pa.map_(pa.string(), pa.int32()), "integer"),
        (datetime(2024, 1, 1), pa.date32(), "date"),
        (1.25, pa.decimal128(4, 2), "Decimal"),
    ],
)
def test_coerce_value_checks_nested_and_lossy_values(value, dtype: pa.DataType, match: str) -> None:
    with pytest.raises(TypeError, match=match):
        coerce_value(value, dtype)


def test_coerce_value_rejects_timezone_mismatch() -> None:
    aware = datetime(2024, 1, 1, 12, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(TypeError, match="timestamp with a timezone"):
        coerce_value(aware, pa.timestamp("us"))
    with pytest.raises(TypeError, match="timestamp without a timezone"):
        coerce_value(aware.replace(tzinfo=None), pa.timestamp("us", tz="UTC"))


@pytest.mark.parametrize(
    ("value", "dtype"),
    [
        (1e39, pa.float32()),
        (-1e39, pa.float32()),
        (10**39, pa.float32()),
        (70000.0, pa.float16()),
        (65520.0, pa.float16()),
        ([0.5, 1e39], pa.list_(pa.float32())),
        ({"x": 1e39}, pa.struct([("x", pa.float32())])),
        (datetime(2020, 1, 1, 0, 0, 0, 500_000), pa.timestamp("s")),
        (datetime(2020, 1, 1, 0, 0, 0, 123_456), pa.timestamp("ms")),
        (datetime(2020, 1, 1, 0, 0, 0, 1, tzinfo=timezone.utc), pa.timestamp("s", tz="UTC")),
        (time(1, 2, 3, 456_789), pa.time32("s")),
        (time(1, 2, 3, 456_789), pa.time32("ms")),
        (timedelta(microseconds=1500), pa.duration("ms")),
        (timedelta(milliseconds=1500), pa.duration("s")),
        (timedelta(microseconds=-1500), pa.duration("ms")),
    ],
)
def test_coerce_value_rejects_values_it_would_change(value: object, dtype: pa.DataType) -> None:
    with pytest.raises(ValueError, match="cannot store"):
        coerce_value(value, dtype)


@pytest.mark.parametrize(
    ("value", "dtype"),
    [(1.5, pa.duration("s")), (True, pa.duration("s")), ("01:00", pa.time64("us")), (1.5, pa.time32("s"))],
)
def test_coerce_value_takes_times_and_durations_as_objects_or_unit_counts(value: object, dtype: pa.DataType) -> None:
    with pytest.raises(TypeError, match="or an integer value"):
        coerce_value(value, dtype)


@pytest.mark.parametrize(
    ("value", "dtype", "expected"),
    [
        (65504.0, pa.float16(), 65504.0),
        (3.4028234663852886e38, pa.float32(), 3.4028234663852886e38),
        (float("-inf"), pa.float32(), float("-inf")),
        (datetime(2020, 1, 1, 0, 0, 1), pa.timestamp("s"), datetime(2020, 1, 1, 0, 0, 1)),
        (datetime(2020, 1, 1, 0, 0, 0, 123_000), pa.timestamp("ms"), datetime(2020, 1, 1, 0, 0, 0, 123_000)),
        (datetime(2020, 1, 1, 0, 0, 0, 1), pa.timestamp("ns"), datetime(2020, 1, 1, 0, 0, 0, 1)),
        (time(1, 2, 3, 456_000), pa.time32("ms"), time(1, 2, 3, 456_000)),
        (3600, pa.time32("s"), time(1)),
        (time(1, 2, 3, 456_789), pa.time64("us"), time(1, 2, 3, 456_789)),
        (timedelta(milliseconds=-2), pa.duration("ms"), timedelta(milliseconds=-2)),
        (timedelta(microseconds=1500), pa.duration("us"), timedelta(microseconds=1500)),
        (5, pa.duration("s"), timedelta(seconds=5)),
    ],
)
def test_coerce_value_keeps_values_the_unit_holds(value: object, dtype: pa.DataType, expected: object) -> None:
    assert coerce_value(value, dtype) == expected
