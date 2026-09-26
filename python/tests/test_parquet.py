from __future__ import annotations

import random
from pathlib import Path
from typing import Annotated

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel, Field

import taco
from taco.container.parquet import DEFAULT_PARQUET_OPTIONS, parquet_writer_options


def test_parquet_options_override_defaults_without_mutating_them() -> None:
    options = parquet_writer_options({"compression": "snappy"})
    assert options["compression"] == "snappy"
    assert DEFAULT_PARQUET_OPTIONS["compression"] == "zstd"


def test_row_group_size_has_one_public_argument() -> None:
    with pytest.raises(ValueError, match="writer argument"):
        parquet_writer_options({"row_group_size": 100})


def chunk_encodings(path: Path, column: str) -> set[str]:
    metadata = pq.ParquetFile(path).metadata
    index = metadata.schema.to_arrow_schema().get_field_index(column)
    return {
        encoding
        for group in range(metadata.num_row_groups)
        for encoding in metadata.row_group(group).column(index).encodings
    }


def test_trial_keeps_the_smallest_encoding() -> None:
    names = [f"district-{index:02d}" for index in range(20)]
    kinds = [random.Random(index).choice(names) for index in range(20_000)]
    table = pa.table({"id": pa.array(range(20_000), pa.uint64()), "kind": kinds})
    options = parquet_writer_options(None, table.schema, table)
    assert options["column_encoding"]["id"] == "DELTA_BINARY_PACKED"
    assert "kind" in options["use_dictionary"]


def test_without_rows_the_type_decides() -> None:
    schema = pa.schema([("x", pa.float32()), ("n", pa.int64()), ("s", pa.string()), ("flag", pa.bool_())])
    options = parquet_writer_options(None, schema)
    assert options["column_encoding"] == {
        "x": "BYTE_STREAM_SPLIT",
        "n": "DELTA_BINARY_PACKED",
        "s": "DELTA_LENGTH_BYTE_ARRAY",
    }
    assert options["use_dictionary"] == []


def test_a_hint_overrides_the_trial() -> None:
    table = pa.table({"kind": ["river", "lake"] * 10_000})
    options = parquet_writer_options(None, table.schema, table, {"kind": "plain"})
    assert options["column_encoding"] == {"kind": "PLAIN"}
    assert options["use_dictionary"] == []


def test_caller_encodings_are_kept() -> None:
    table = pa.table({"kind": ["river", "lake"]})
    options = parquet_writer_options({"use_dictionary": False}, table.schema, table)
    assert options["use_dictionary"] is False
    assert "column_encoding" not in options


def test_column_encoding_disables_the_default_dictionary(tmp_path: Path) -> None:
    table = pa.table({"kind": ["river", "lake"] * 10})
    options = parquet_writer_options({"column_encoding": {"kind": "PLAIN"}}, table.schema, table)
    assert options["use_dictionary"] is False
    path = tmp_path / "plain.parquet"
    pq.write_table(table, path, **options)
    assert "RLE_DICTIONARY" not in chunk_encodings(path, "kind")


def test_byte_stream_split_disables_the_default_dictionary(tmp_path: Path) -> None:
    table = pa.table({"value": [float(index) for index in range(20)]})
    options = parquet_writer_options({"use_byte_stream_split": True}, table.schema, table)
    assert options["use_dictionary"] is False
    path = tmp_path / "byte-stream-split.parquet"
    pq.write_table(table, path, **options)
    assert "BYTE_STREAM_SPLIT" in chunk_encodings(path, "value")


def test_unknown_encoding_is_rejected() -> None:
    with pytest.raises(ValueError, match="encoding must be one of"):
        taco.Encoding("zip")


class Place(BaseModel):
    name: Annotated[str, taco.Encoding("dictionary")] = Field(description="Place name")
    code: str = Field(description="Unique code")


def test_written_encodings_follow_hints_and_stay_readable(tmp_path: Path) -> None:
    contract = taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", place=Place)])
    collection = taco.Collection(
        contract=contract,
        id="places",
        description="Encoding hints",
        licenses=["MIT"],
        providers=["TACO tests"],
    )
    names = ["Lima", "Cusco", "Puno"]
    with taco.open_writer(collection, tmp_path / "places") as writer:
        writer.extend(
            taco.Sample(
                id=f"s{index}",
                assets=b"x",
                metadata=taco.Metadata(place=Place(name=names[index % 3], code=f"MT10km_{index:06d}")),
            )
            for index in range(300)
        )
        writer.run()

    path = tmp_path / "places" / "METADATA" / "sample.parquet"
    assert "RLE_DICTIONARY" in chunk_encodings(path, "place:name")
    assert b"taco:encoding" not in (pq.read_schema(path).field("place:name").metadata or {})
    for column in pq.read_schema(path).names:
        assert "DELTA_BYTE_ARRAY" not in chunk_encodings(path, column)
    assert pq.read_table(path).column("place:name").to_pylist() == [names[index % 3] for index in range(300)]


@pytest.mark.parametrize("codec", ["snappy", "gzip", "none", None])
def test_the_zstd_level_is_not_given_to_other_codecs(codec: str | None) -> None:
    table = pa.table({"a": [1, 2, 3]})
    options = parquet_writer_options({"compression": codec}, table.schema, table)
    assert "compression_level" not in options
    pq.write_table(table, pa.BufferOutputStream(), **options)


def test_zstd_gets_the_default_level_unless_one_is_given() -> None:
    assert parquet_writer_options(None)["compression_level"] == 9
    assert parquet_writer_options({"compression_level": 3})["compression_level"] == 3


def test_trials_do_not_reach_the_metadata_collector(tmp_path: Path) -> None:
    collected: list[object] = []
    contract = taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", place=Place)])
    collection = taco.Collection(
        contract=contract, id="collector", description="Collector", licenses=["MIT"], providers=["TACO tests"]
    )
    with taco.open_writer(
        collection, tmp_path / "collector", parquet_options={"metadata_collector": collected}
    ) as writer:
        writer.add(taco.Sample(id="s0", assets=b"x", metadata=taco.Metadata(place=Place(name="Lima", code="a"))))
        writer.run()
    assert len(collected) == 2
