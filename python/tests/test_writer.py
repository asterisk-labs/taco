from __future__ import annotations

import io
import json
import struct
import zipfile
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import taco
from taco._view import open_view as open_dataset
from taco.errors import SampleError, WriterError
from taco.reader.engine import connect
from taco.writer import WriterState

PRIORITY = [
    "COLLECTION.json",
    "METADATA/collection.parquet",
    "METADATA/sample.parquet",
    "METADATA/sample__before.parquet",
    "METADATA/sample__after.parquet",
]


def test_run_builds_tacozip_once(tmp_path: Path, collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "dataset") as writer:
        assert writer.state is WriterState.OPEN
        assert writer.add(make_sample(0)) == 0
        assert writer.extend([make_sample(1), make_sample(2, 2)]) == 3
        assert writer.sample_count == 3
        assert not list(writer._stage.rglob("*.parquet"))
        first = writer.run()
        second = writer.run()
        assert writer.state is WriterState.SUCCEEDED
        with pytest.raises(WriterError):
            writer.add(make_sample(3))
    assert first is second
    assert first.path.name == "dataset.zip"
    assert first.samples == 3 and first.data_files == 14 and first.metadata_files == 4
    assert not first.partitioned
    assert writer.state is WriterState.SUCCEEDED


def test_archive_layout_and_index(archive: Path) -> None:
    # The reader is the authority on the cozip layer, so the profile and the
    # priority set are checked through it rather than by a second parser.
    assert connect().execute("SELECT cozip_profile(?)", [str(archive)]).fetchone()[0] == "taco"
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        infos = zf.infolist()
    assert names[0] == "__cozip__"
    assert names[-len(PRIORITY) :] == PRIORITY
    assert all(info.compress_type == zipfile.ZIP_STORED for info in infos)
    assert not any(info.is_dir() for info in infos)
    if "__cozip_padding__" in names:
        assert names.index("__cozip_padding__") < names.index("COLLECTION.json")
    raw = archive.read_bytes()
    assert raw[-22:-18] == b"PK\x05\x06" and raw[-2:] == b"\x00\x00"
    assert struct.unpack_from("<Q", raw, 43)[0] != 0


def test_metadata_tables_and_offsets(archive: Path, tmp_path: Path) -> None:
    dataset = open_dataset(archive)
    assert dataset.container == "zip" and dataset.sample_count == 4
    collection = dataset.level("collection")
    assert collection.column_names[:2] == ["internal:current_id", "internal:relative_path"]
    assert collection.column("internal:relative_path").to_pylist() == ["0", "1", "2", "3"]
    # Field descriptions and the taco:level tag live in the Parquet schema and
    # in COLLECTION.json; DuckDB does not surface Parquet key-value metadata,
    # so the reader cannot return them.
    assert collection.schema.metadata is None

    sample = dataset.level("sample")
    assert sample.column_names[:5] == [
        "internal:current_id",
        "internal:parent_id",
        "internal:relative_path",
        "internal:offset",
        "internal:size",
    ]
    # 3 fixed children + variable extras (0, 1, 2, 0)
    assert sample.num_rows == 4 * 3 + 0 + 1 + 2 + 0
    rows = sample.to_pylist()
    assert rows[0]["internal:relative_path"] == "0/before" and rows[0]["internal:offset"] is None
    assert rows[2]["internal:relative_path"] == "0/mask.tif" and rows[2]["internal:offset"] is not None
    assert [row["internal:parent_id"] for row in rows[:3]] == [0, 0, 0]
    assert rows[3]["internal:parent_id"] == 1

    before = dataset.level("sample/before")
    before_rows = before.to_pylist()
    assert before.num_rows == 8
    # parent is the row id of "<i>/before" in sample.parquet
    parent_of_first = before_rows[0]["internal:parent_id"]
    assert rows[parent_of_first]["internal:relative_path"] == "0/before"
    assert before_rows[0]["resolution"] == 10

    raw = archive.read_bytes()
    with zipfile.ZipFile(archive) as zf:
        for row in dataset.iter_data_rows():
            assert raw[row.offset : row.offset + row.size] == zf.read(row.archive_name)

    collection_json = json.loads(zipfile.ZipFile(archive).read("COLLECTION.json"))
    assert collection_json["id"] == "tiny-change"
    assert collection_json["labels:num_classes"] == 2
    assert "extent" not in collection_json


def test_extent_is_only_what_the_collection_declares(tmp_path: Path, collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "none.zip") as writer:
        writer.add(make_sample(0))
        result = writer.run()
    assert open_dataset(result.path).collection.extent is None

    fixed = collection.replace(extent={"spatial": [0, 0, 1, 1], "temporal": ["2024-01-01", "2024-06-01"]})
    with taco.open_writer(fixed, tmp_path / "fixed.zip") as writer:
        writer.add(make_sample(0))
        result = writer.run()
    extent = open_dataset(result.path).collection.extent
    assert extent.spatial == (0.0, 0.0, 1.0, 1.0)
    assert extent.temporal == ("2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z")


def test_inline_bytes_assets_are_materialized(tmp_path: Path) -> None:
    contract = taco.Contract(structure=["a.bin", "b.bin"], metadata={"collection": {"n": "int32"}})
    collection = taco.Collection(
        contract=contract,
        id="inline",
        dataset_version="0.1.0",
        description="d",
        licenses=["MIT"],
        providers=["p"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "inline.zip") as writer:
        writer.add(taco.Sample(assets={"a.bin": b"aaaa", "b.bin": bytearray(b"bb")}, metadata={"collection": {"n": 1}}))
        result = writer.run()
    with zipfile.ZipFile(result.path) as zf:
        assert zf.read("DATA/0/a.bin") == b"aaaa"
        assert zf.read("DATA/0/b.bin") == b"bb"


def test_null_structure_archive(tmp_path: Path) -> None:
    contract = taco.Contract(structure=None, metadata={"collection": {"label": ["int8", "class id"]}})
    collection = taco.Collection(
        contract=contract,
        id="null",
        dataset_version="0.1.0",
        description="d",
        licenses=["MIT"],
        providers=["p"],
        tasks=["classification"],
    )
    with taco.open_writer(collection, tmp_path / "null.zip") as writer:
        for index in range(3):
            writer.add(taco.Sample(assets=b"payload-%d" % index * 4, metadata={"collection": {"label": index}}))
        result = writer.run()
    dataset = open_dataset(result.path)
    assert dataset.levels == ("collection",)
    table = dataset.level("collection")
    assert table.column_names == [
        "internal:current_id",
        "internal:relative_path",
        "internal:offset",
        "internal:size",
        "label",
    ]
    raw = result.path.read_bytes()
    for row in dataset.iter_data_rows():
        assert raw[row.offset : row.offset + row.size] == b"payload-%d" % row.sample_index * 4
    with zipfile.ZipFile(result.path) as zf:
        assert "DATA/1" in zf.namelist()


def test_add_rejects_bad_sources(tmp_path: Path, collection, make_sample) -> None:
    good = make_sample(0)
    with taco.open_writer(collection, tmp_path / "bad.zip") as writer:
        empty = tmp_path / "empty.tif"
        empty.write_bytes(b"")
        assets = {asset.path: asset.source for asset in good.assets}
        assets["mask.tif"] = empty
        with pytest.raises(SampleError, match="zero-byte"):
            writer.add(taco.Sample(assets=assets, metadata=good.metadata))
        assets["mask.tif"] = tmp_path / "missing.tif"
        with pytest.raises(FileNotFoundError):
            writer.add(taco.Sample(assets=assets, metadata=good.metadata))
        with pytest.raises(TypeError):
            writer.add("nope")  # type: ignore[arg-type]
        assert writer.sample_count == 0
        with pytest.raises(WriterError, match="without samples"):
            writer.run()
        assert writer.state is WriterState.OPEN
    assert writer.state is WriterState.CLOSED


def test_output_rules(tmp_path: Path, collection, make_sample) -> None:
    with pytest.raises(ValueError, match=r"end in \.zip"):
        taco.open_writer(collection, tmp_path / "dataset.cozip")
    assert taco.open_writer(collection, tmp_path / "dataset").output.name == "dataset.zip"
    output = tmp_path / "exists.zip"
    output.write_bytes(b"old")
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
        with pytest.raises(FileExistsError):
            writer.run()
    assert output.read_bytes() == b"old"
    with taco.open_writer(collection, output, overwrite=True) as writer:
        writer.add(make_sample(0))
        writer.run()
    assert output.read_bytes()[:4] == b"PK\x03\x04"
    assert not list(tmp_path.glob(".exists.zip.*"))


def test_unicode_source_and_output_paths(tmp_path: Path, collection, make_sample) -> None:
    sample = make_sample(0)
    assets = {asset.path: asset.source for asset in sample.assets}
    source = tmp_path / "niño.tif"
    source.write_bytes(b"pixels")
    assets["mask.tif"] = source

    with taco.open_writer(collection, tmp_path / "colección.zip") as writer:
        writer.add(taco.Sample(assets=assets, metadata=sample.metadata))
        result = writer.run()

    assert result.path.read_bytes()[:4] == b"PK\x03\x04"


def test_no_overwrite_closes_publish_race(
    tmp_path: Path, collection, make_sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    import taco.writer.archive as archive_module

    output = tmp_path / "race.zip"
    native_write = archive_module.cozip_write

    def write_while_another_process_publishes(*args, **kwargs) -> None:
        native_write(*args, **kwargs)
        output.write_bytes(b"other writer")

    monkeypatch.setattr(archive_module, "cozip_write", write_while_another_process_publishes)
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
        with pytest.raises(FileExistsError, match="already exists"):
            writer.run()

    assert output.read_bytes() == b"other writer"
    assert not list(tmp_path.glob(".race.zip.*"))


def test_close_never_builds_implicitly(tmp_path: Path, collection, make_sample) -> None:
    output = tmp_path / "not-built.zip"
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
    assert not output.exists()
    assert writer.state is WriterState.CLOSED
    with pytest.raises(WriterError):
        writer.run()


def test_mapping_samples_and_timestamps(tmp_path: Path) -> None:
    contract = taco.Contract(structure=["a.bin"], metadata={"collection": {"t": "timestamp[us]", "d": "date32"}})
    collection = taco.Collection(
        contract=contract,
        id="ts",
        dataset_version="0.1.0",
        description="d",
        licenses=["MIT"],
        providers=["p"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "ts.zip", row_group_size=1, batch_size=1) as writer:
        writer.add(
            {
                "assets": {"a.bin": b"x"},
                "metadata": {"collection": {"t": datetime(2024, 5, 6, 7, 8), "d": datetime(2024, 5, 6).date()}},
            }
        )
        writer.add({"assets": {"a.bin": b"y"}, "metadata": {"collection": {"t": 1_700_000_000_000_000, "d": None}}})
        result = writer.run()
    with zipfile.ZipFile(result.path) as zf:
        table = pq.read_table(io.BytesIO(zf.read("METADATA/collection.parquet")))
    assert table.num_rows == 2
    assert (
        pq.ParquetFile(io.BytesIO(zipfile.ZipFile(result.path).read("METADATA/collection.parquet"))).num_row_groups == 2
    )
    assert table.column("t").to_pylist()[0] == datetime(2024, 5, 6, 7, 8)
    assert table.column("d").to_pylist()[1] is None


def test_deeply_nested_structure_parent_links(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["s2/bands/B02.tif", "s2/bands/B03.tif", "s2/cloud*[0,2].tif", "label.tif"],
        metadata={
            "sample": {"role": "string"},
            "sample/s2": {"n": "int32"},
            "sample/s2/bands": {"wavelength": "double"},
        },
    )
    collection = taco.Collection(
        contract=contract,
        id="deep",
        dataset_version="0.1.0",
        description="d",
        licenses=["MIT"],
        providers=["p"],
        tasks=["other"],
    )
    assert contract.levels == ("collection", "sample", "sample/s2", "sample/s2/bands")

    def sample(index: int, clouds: int) -> taco.Sample:
        assets = {"s2/bands/B02.tif": b"b02" * (index + 1), "s2/bands/B03.tif": b"b03", "label.tif": b"lbl"}
        assets.update({f"s2/cloud{k}.tif": b"cloud" for k in range(clouds)})
        return taco.Sample(
            assets=assets,
            metadata={
                "sample": {"s2": {"role": "imagery"}, "label.tif": {"role": "label"}},
                "sample/s2": {"bands": {"n": 2}, **{f"cloud{k}.tif": {"n": k} for k in range(clouds)}},
                "sample/s2/bands": {"B02.tif": {"wavelength": 490.0}, "B03.tif": {"wavelength": 560.0}},
            },
        )

    with taco.open_writer(collection, tmp_path / "deep.zip", batch_size=2) as writer:
        writer.add(sample(0, 2))
        writer.add(sample(1, 0))
        writer.add(sample(2, 1))
        result = writer.run()

    dataset = open_dataset(result.path)
    level1 = dataset.level("sample").to_pylist()
    level2 = dataset.level("sample/s2").to_pylist()
    level3 = dataset.level("sample/s2/bands").to_pylist()
    assert [row["internal:relative_path"] for row in level1] == [
        "0/s2",
        "0/label.tif",
        "1/s2",
        "1/label.tif",
        "2/s2",
        "2/label.tif",
    ]
    assert [row["internal:relative_path"] for row in level2] == [
        "0/s2/bands",
        "0/s2/cloud0.tif",
        "0/s2/cloud1.tif",
        "1/s2/bands",
        "2/s2/bands",
        "2/s2/cloud0.tif",
    ]
    assert [row["internal:parent_id"] for row in level2] == [0, 0, 0, 2, 4, 4]
    assert [row["internal:relative_path"] for row in level3] == [
        "0/s2/bands/B02.tif",
        "0/s2/bands/B03.tif",
        "1/s2/bands/B02.tif",
        "1/s2/bands/B03.tif",
        "2/s2/bands/B02.tif",
        "2/s2/bands/B03.tif",
    ]
    assert [row["internal:parent_id"] for row in level3] == [0, 0, 3, 3, 4, 4]
    assert all(row["internal:offset"] is None for row in level2 if row["internal:relative_path"].endswith("bands"))
    assert all(row["internal:offset"] is not None for row in level3)
    report = taco.validate(result.path)
    assert report.ok, report
    raw = result.path.read_bytes()
    first = next(row for row in dataset.iter_data_rows() if row.relative_path == "0/s2/bands/B02.tif")
    assert raw[first.offset : first.offset + first.size] == b"b02"
