from __future__ import annotations

import io
import json
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel

import taco
from taco._view import open_view
from taco.errors import SampleError, WriterError


def point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def test_zip_end_to_end(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "data.zip"
    with taco.open_writer(collection, output, batch_size=2) as writer:
        assert writer.add(make_sample(0)) == 0
        assert writer.extend([make_sample(1, 1), make_sample(2, 2)]) == 3
        result = writer.run()
        assert writer.run() is result
    assert result.path == output.resolve()
    assert result.samples == 3
    assert result.data_files == 15
    assert taco.validate(output).ok

    dataset = open_view(output)
    assert dataset.levels == ("sample", "children", "children/before", "children/after")
    assert dataset.sample_count == 3
    assert dataset.collection.extent == taco.contract.Extent(
        (-76, -12, -74, -11.8),
        ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
    )
    assert dataset.level("sample").column("majortom:code").null_count == 0
    assert dataset.level("children").num_rows == 3 * 3 + 3


def test_zip_layout_and_offsets(archive: Path) -> None:
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        assert names[0] == "__cozip__"
        assert names[-5:] == [
            "COLLECTION.json",
            "METADATA/sample.parquet",
            "METADATA/children.parquet",
            "METADATA/children__before.parquet",
            "METADATA/children__after.parquet",
        ]
        collection = json.loads(zipped.read("COLLECTION.json"))
        schema = pq.read_schema(io.BytesIO(zipped.read("METADATA/sample.parquet")))
    assert collection["taco:version"] == "3.0.0"
    assert collection["labels:num_classes"] == 2
    assert schema.metadata == {b"taco:level": b"sample"}
    assert schema.field("ml:cloud_cover").nullable

    raw = archive.read_bytes()
    dataset = open_view(archive)
    for row in dataset.iter_data_rows():
        with zipfile.ZipFile(archive) as zipped:
            expected = zipped.read(row.archive_name)
        assert raw[row.offset : row.offset + row.size] == expected


def test_folder_end_to_end(folder_dataset: Path) -> None:
    dataset = open_view(folder_dataset)
    assert dataset.container == "folder"
    assert dataset.sample_count == 4
    assert "internal:offset" not in dataset.level("children").column_names
    assert (folder_dataset / "DATA/0/before/B02.tif").is_file()
    assert dataset.collection.extent == taco.contract.Extent(
        (-76, -12, -73, -11.7),
        ("2024-01-01T00:00:00Z", "2024-01-04T00:00:00Z"),
    )
    assert taco.validate(folder_dataset).ok


def test_folder_append(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "data"
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
        writer.run()
    updated = collection.replace(dataset_version="1.1.0")
    with taco.open_writer(updated, output, append=True) as writer:
        writer.extend([make_sample(1), make_sample(2)])
        writer.run()
    dataset = open_view(output)
    assert dataset.sample_count == 3
    assert dataset.collection.dataset_version == "1.1.0"
    assert dataset.collection.extent == taco.contract.Extent(
        (-76, -12, -74, -11.8),
        ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
    )
    assert taco.validate(output).ok


def test_failed_append_keeps_existing_dataset(
    tmp_path: Path, collection: taco.Collection, make_sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "data"
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
        writer.run()
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}

    import taco.writer.folder as folder_module

    def fail(*args, **kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(folder_module, "publish_many", fail)
    with taco.open_writer(collection.replace(dataset_version="1.1.0"), output, append=True) as writer:
        writer.add(make_sample(1))
        with pytest.raises(OSError, match="disk full"):
            writer.run()

    after = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert after == before


def test_writer_mode_is_selected_from_path(tmp_path: Path, collection: taco.Collection) -> None:
    with taco.open_writer(collection, tmp_path / "folder") as writer:
        assert writer.__class__.__name__ == "_FolderWriter"
    with taco.open_writer(collection, tmp_path / "archive.zip") as writer:
        assert writer.__class__.__name__ == "_ArchiveWriter"
    with pytest.raises(ValueError, match="must end"):
        taco.open_writer(collection, tmp_path / "archive.taco")
    with pytest.raises(ValueError, match="immutable"):
        taco.open_writer(collection, tmp_path / "archive.zip", append=True)


def test_context_manager_does_not_build(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "data.zip"
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
    assert not output.exists()
    with pytest.raises(WriterError):
        writer.run()


def test_invalid_sources_are_rejected(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    sample = make_sample(0)
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assets = list(sample.assets)
    assets[-1] = taco.Asset(empty, path=assets[-1].path, metadata=assets[-1].metadata)
    with taco.open_writer(collection, tmp_path / "data.zip") as writer:
        with pytest.raises(SampleError, match="zero-byte"):
            writer.add(taco.Sample(assets=assets, metadata=sample.metadata, folders=sample.folders))
        with pytest.raises(WriterError, match="without samples"):
            writer.run()


def test_writer_rejects_tacocat_sources(tmp_path: Path, collection: taco.Collection) -> None:
    with pytest.raises(ValueError, match="reserved for TACOCAT"):
        taco.open_writer(collection.replace(sources={"samples": 0, "partitions": []}), tmp_path / "data.zip")


def test_single_file_dataset(tmp_path: Path) -> None:
    class Label(BaseModel):
        value: int

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", label=Label)),
    )
    collection = taco.Collection(
        contract=contract,
        id="single",
        dataset_version="1.0.0",
        description="Single files",
        licenses=["MIT"],
        providers=["me"],
        tasks=["classification"],
    )
    output = tmp_path / "single.zip"
    with taco.open_writer(collection, output) as writer:
        writer.add(taco.Sample(assets=b"one", metadata=taco.Metadata(label=Label(value=1))))
        writer.run()
    table = open_view(output).level("sample")
    assert table.column_names[:4] == [
        "internal:current_id",
        "internal:relative_path",
        "internal:offset",
        "internal:size",
    ]
    assert zipfile.ZipFile(output).read("DATA/0") == b"one"


def test_stac_generates_extent(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", stac=taco.metadata.sample.STAC)),
    )
    collection = taco.Collection(
        contract=contract,
        id="spatiotemporal",
        dataset_version="1.0.0",
        description="Spatiotemporal samples",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
        extent={"spatial": [0, 0, 0, 0]},
    )
    records = [
        (94, -10, datetime(2024, 1, 2, tzinfo=timezone.utc), datetime(2024, 1, 5, tzinfo=timezone.utc)),
        (-178, 20, datetime(2024, 1, 1, tzinfo=timezone.utc), None),
        (-3, 5, datetime(2024, 1, 3, tzinfo=timezone.utc), None),
    ]
    with taco.open_writer(collection, tmp_path / "data.zip", batch_size=1) as writer:
        for lon, lat, start, end in records:
            location = point(lon, lat)
            writer.add(
                taco.Sample(
                    assets=b"x",
                    metadata=taco.Metadata(
                        stac=taco.metadata.sample.STAC(
                            crs="EPSG:4326",
                            geometry=location,
                            centroid=location,
                            time_start=start,
                            time_end=end,
                        )
                    ),
                )
            )
        writer.run()

    assert open_view(tmp_path / "data.zip").collection.extent == taco.contract.Extent(
        (-3, -10, -178, 20),
        ("2024-01-01T00:00:00Z", "2024-01-05T00:00:00Z"),
    )


def test_empty_stac_summary_removes_extent(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", stac=taco.metadata.sample.STAC | None)),
    )
    collection = taco.Collection(
        contract=contract,
        id="without-location",
        dataset_version="1.0.0",
        description="Sample without location",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
        extent={"spatial": [0, 0, 0, 0]},
    )
    with taco.open_writer(collection, tmp_path / "data.zip") as writer:
        writer.add(taco.Sample(assets=b"x"))
        writer.run()

    assert open_view(tmp_path / "data.zip").collection.extent is None


def test_optional_only_structure_can_have_no_data(tmp_path: Path) -> None:
    collection = taco.Collection(
        contract=taco.Contract(structure=["image*[0,2].tif"]),
        id="empty-sample",
        dataset_version="1.0.0",
        description="Optional files",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
    )
    output = tmp_path / "empty-sample.zip"
    with taco.open_writer(collection, output) as writer:
        writer.add(taco.Sample())
        writer.run()
    assert open_view(output).sample_count == 1
    assert taco.validate(output).ok


def test_partition_by_sample_metadata(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "parts.zip"
    with taco.open_writer(collection, output, partition_by="ml:split") as writer:
        writer.extend(make_sample(index) for index in range(4))
        result = writer.run()
    assert result.path == (tmp_path / ".tacocat").resolve()
    assert {path.name for path in result.parts} == {"parts_train.zip", "parts_val.zip"}
    extents = {path.name: open_view(path).collection.extent for path in result.parts}
    assert extents == {
        "parts_train.zip": taco.contract.Extent(
            (-76, -12, -74, -11.8),
            ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
        ),
        "parts_val.zip": taco.contract.Extent(
            (-75, -11.9, -73, -11.7),
            ("2024-01-02T00:00:00Z", "2024-01-04T00:00:00Z"),
        ),
    }
    assert open_view(result.path).collection.extent == taco.contract.Extent(
        (-76, -12, -73, -11.7),
        ("2024-01-01T00:00:00Z", "2024-01-04T00:00:00Z"),
    )
    assert taco.validate(result.path).ok


def test_partition_by_derived_metadata(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "grid.zip"
    with taco.open_writer(collection, output, partition_by="majortom:code", batch_size=2) as writer:
        writer.extend(make_sample(index) for index in range(3))
        result = writer.run()
    assert len(result.parts) == 3
    assert all(open_view(path).level("sample").column("majortom:code").null_count == 0 for path in result.parts)
    assert taco.validate(result.path).ok


def test_partition_by_size(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "parts.zip"
    with taco.open_writer(collection, output, partition_size=1) as writer:
        writer.extend(make_sample(index) for index in range(3))
        result = writer.run()
    assert len(result.parts) == 3
    assert open_view(result.path).sample_count == 3


def test_partition_options_are_checked(tmp_path: Path, collection: taco.Collection) -> None:
    with pytest.raises(ValueError, match="either"):
        taco.open_writer(collection, tmp_path / "a.zip", partition_size=1, partition_by="ml:split")
    with pytest.raises(ValueError, match="sample metadata"):
        taco.open_writer(collection, tmp_path / "a.zip", partition_by="missing:value")
    with pytest.raises(ValueError, match="only valid for ZIP"):
        taco.open_writer(collection, tmp_path / "folder", partition_size=1)


def test_overwrite_zip(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "data.zip"
    output.write_bytes(b"old")
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
        with pytest.raises(FileExistsError):
            writer.run()
    assert output.read_bytes() == b"old"
    with taco.open_writer(collection, output, overwrite=True) as writer:
        writer.add(make_sample(0))
        writer.run()
    assert output.read_bytes().startswith(b"PK")


def test_folder_hardlinks(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    sample = make_sample(0)
    output = tmp_path / "linked"
    with taco.open_writer(collection, output, link=True) as writer:
        writer.add(sample)
        writer.run()
    source = sample.assets[0].source
    assert isinstance(source, Path)
    assert (output / "DATA/0/before/B02.tif").stat().st_ino == source.stat().st_ino


def test_append_requires_same_contract(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    output = tmp_path / "data"
    with taco.open_writer(collection, output) as writer:
        writer.add(make_sample(0))
        writer.run()
    changed = collection.replace(contract=taco.Contract(structure=["a.bin"]), dataset_version="2.0.0")
    with taco.open_writer(changed, output, append=True) as writer:
        writer.add(taco.Sample(assets=[taco.Asset(b"x", path="a.bin")]))
        with pytest.raises(WriterError, match="different contract"):
            writer.run()

    renamed = collection.replace(id="other", dataset_version="1.1.0")
    with taco.open_writer(renamed, output, append=True) as writer:
        writer.add(make_sample(1))
        with pytest.raises(WriterError, match="dataset id"):
            writer.run()

    for version in ("1.0.0", "2.0.0"):
        with taco.open_writer(collection.replace(dataset_version=version), output, append=True) as writer:
            writer.add(make_sample(1))
            with pytest.raises(WriterError, match="higher minor"):
                writer.run()


def test_folder_destination_checks(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"x")
    with taco.open_writer(collection, file) as writer:
        writer.add(make_sample(0))
        with pytest.raises(WriterError, match="not a directory"):
            writer.run()

    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "mine.txt").write_text("keep")
    with taco.open_writer(collection, directory) as writer:
        writer.add(make_sample(0))
        with pytest.raises(FileExistsError, match="not empty"):
            writer.run()
    with taco.open_writer(collection, directory, overwrite=True) as writer:
        writer.add(make_sample(0))
        with pytest.raises(WriterError, match="refusing"):
            writer.run()


def test_append_options_are_checked(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        taco.open_writer(collection, tmp_path / "folder", append=True, overwrite=True)
    with taco.open_writer(collection, tmp_path / "missing", append=True) as writer:
        writer.add(make_sample(0))
        with pytest.raises(WriterError, match="existing FOLDER"):
            writer.run()
    with pytest.raises(ValueError, match="only valid"):
        taco.open_writer(collection, tmp_path / "data.zip", link=True)
