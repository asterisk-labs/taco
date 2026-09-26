from __future__ import annotations

import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_PARQUET_OPTIONS: dict[str, Any] = {
    "compression": "zstd",
    "write_statistics": True,
}
ZSTD_LEVEL = 9
ENCODING_KEY = b"taco:encoding"
ENCODINGS = ("dictionary", "plain", "byte_stream_split", "delta")
_CALLER_ENCODINGS = frozenset({"use_dictionary", "column_encoding", "use_byte_stream_split"})
_TRIAL_OPTIONS = frozenset(
    {
        "allow_truncated_timestamps",
        "coerce_timestamps",
        "compression",
        "compression_level",
        "data_page_size",
        "data_page_version",
        "dictionary_pagesize_limit",
        "store_decimal_as_integer",
        "use_compliant_nested_type",
        "write_batch_size",
        "write_statistics",
    }
)
_FLOATS = frozenset({"FLOAT", "DOUBLE"})
_INTEGERS = frozenset({"INT32", "INT64"})
_TRIAL_ROWS = 10_000


@dataclass(frozen=True)
class Encoding:
    """Parquet encoding hint."""

    name: str

    def __post_init__(self) -> None:
        if self.name not in ENCODINGS:
            raise ValueError(f"encoding must be one of {', '.join(ENCODINGS)}, got {self.name!r}")

    @property
    def metadata(self) -> dict[bytes, bytes]:
        return {ENCODING_KEY: self.name.encode()}


def _leaf_count(dtype: pa.DataType) -> int:
    if pa.types.is_struct(dtype):
        return sum(_leaf_count(field.type) for field in dtype)
    if pa.types.is_map(dtype):
        return _leaf_count(dtype.key_type) + _leaf_count(dtype.item_type)
    if pa.types.is_list(dtype) or pa.types.is_large_list(dtype) or pa.types.is_fixed_size_list(dtype):
        return _leaf_count(dtype.value_type)
    return 1


def _leaves(field: pa.Field) -> list[tuple[str, str]]:
    buffer = io.BytesIO()
    cast(Any, pq).write_table(pa.schema([field]).empty_table(), buffer)
    buffer.seek(0)
    parquet = cast(Any, pq).ParquetFile(buffer).schema
    assert len(parquet) == _leaf_count(field.type)
    return [(parquet.column(index).path, parquet.column(index).physical_type) for index in range(len(parquet))]


def _plan(encoding: str, leaves: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    if encoding == "dictionary":
        return [path for path, kind in leaves if kind != "BOOLEAN"], {}
    encodings = {}
    for path, kind in leaves:
        if kind == "BOOLEAN":
            continue
        if encoding == "byte_stream_split" and kind in _FLOATS:
            encodings[path] = "BYTE_STREAM_SPLIT"
        elif encoding == "delta" and kind in _INTEGERS:
            encodings[path] = "DELTA_BINARY_PACKED"
        # Not DELTA_BYTE_ARRAY: hyparquet reads it only in version 2 data pages.
        elif encoding == "delta" and kind == "BYTE_ARRAY":
            encodings[path] = "DELTA_LENGTH_BYTE_ARRAY"
        else:
            encodings[path] = "PLAIN"
    return [], encodings


def _default(leaves: list[tuple[str, str]]) -> str:
    return "byte_stream_split" if any(kind in _FLOATS for _, kind in leaves) else "delta"


def _trial(column: pa.Table, leaves: list[tuple[str, str]], options: Mapping[str, Any]) -> str:
    sizes = []
    for encoding in ENCODINGS:
        dictionary, encodings = _plan(encoding, leaves)
        buffer = io.BytesIO()
        cast(Any, pq).write_table(column, buffer, **options, use_dictionary=dictionary, column_encoding=encodings)
        sizes.append((buffer.tell(), encoding))
    return min(sizes)[1]


def encoding_hints(fields: Iterable[pa.Field]) -> dict[str, str]:
    return {
        field.name: field.metadata[ENCODING_KEY].decode()
        for field in fields
        if field.metadata and ENCODING_KEY in field.metadata
    }


def choose_encodings(
    schema: pa.Schema,
    sample: pa.Table | None,
    options: Mapping[str, Any],
    hints: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    dictionary: list[str] = []
    encodings: dict[str, str] = {}
    trial_options = {key: value for key, value in options.items() if key in _TRIAL_OPTIONS}
    rows = sample.slice(0, _TRIAL_ROWS) if sample is not None and sample.num_rows else None
    for field in schema:
        leaves = _leaves(field)
        hint = (hints or {}).get(field.name)
        if hint is not None:
            encoding = Encoding(hint).name
        elif rows is not None:
            encoding = _trial(rows.select([field.name]), leaves, trial_options)
        else:
            encoding = _default(leaves)
        column_dictionary, column_encodings = _plan(encoding, leaves)
        dictionary.extend(column_dictionary)
        encodings.update(column_encodings)
    return dictionary, encodings


def parquet_writer_options(
    options: Mapping[str, Any] | None,
    schema: pa.Schema | None = None,
    sample: pa.Table | None = None,
    hints: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if options and "row_group_size" in options:
        raise ValueError("pass row_group_size as a writer argument, not in parquet_options")
    result = DEFAULT_PARQUET_OPTIONS | dict(options or {})
    codec = result["compression"]
    if "compression_level" not in result and isinstance(codec, str) and codec.lower() == "zstd":
        result["compression_level"] = ZSTD_LEVEL
    if schema is None:
        return result
    caller_options = set(options or {})
    if _CALLER_ENCODINGS & caller_options:
        # PyArrow's dictionary default overrides the other encodings.
        if "use_dictionary" not in caller_options and (
            "column_encoding" in caller_options or bool(result.get("use_byte_stream_split"))
        ):
            result["use_dictionary"] = False
        return result
    result["use_dictionary"], result["column_encoding"] = choose_encodings(schema, sample, result, hints)
    return result
