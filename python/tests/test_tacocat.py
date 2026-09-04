from __future__ import annotations

from pathlib import Path

import pytest

import taco
from taco._view import open_view as open_dataset
from taco.errors import ConsolidationError, WriterError


def test_partition_by_field(tmp_path: Path, collection, make_sample) -> None:
    output = tmp_path / "parts" / "ds.zip"
    with taco.open_writer(collection, output, partition_by="split") as writer:
        for index in range(5):
            writer.add(make_sample(index, index % 2))
        result = writer.run()
    assert result.partitioned
    assert result.path == tmp_path / "parts" / ".tacocat"
    assert [part.name for part in result.parts] == ["ds_train.zip", "ds_val.zip"]
    assert result.samples == 5
    assert not output.exists()

    train = open_dataset(result.parts[0])
    assert train.sample_count == 3
    assert train.level("collection").column("internal:relative_path").to_pylist() == ["0", "1", "2"]

    catalog = open_dataset(result.path)
    assert catalog.container == "tacocat"
    assert catalog.sample_count == 5
    sources = catalog.collection_json["taco:sources"]
    assert sources["count"] == 2 and sources["files"] == ["ds_train.zip", "ds_val.zip"]
    assert sources["extents"][1]["samples"] == 2
    sample = catalog.level("sample")
    assert sample.column_names[-1] == "internal:source_file"
    assert set(sample.column("internal:source_file").to_pylist()) == {"ds_train.zip", "ds_val.zip"}
    row = next(catalog.iter_data_rows())
    assert (tmp_path / "parts" / row.source_file).is_file()
    assert row.offset is not None and row.size > 0
    report = taco.validate(result.path)
    assert report.ok, report


def test_partition_by_size(tmp_path: Path, collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "size" / "ds.zip", partition_size="2KB") as writer:
        sizes = [writer.add(make_sample(index)) for index in range(4)]
        assert sizes == [0, 1, 2, 3]
        result = writer.run()
    assert len(result.parts) >= 2
    assert all(part.name.startswith("ds_part") for part in result.parts)
    assert sum(open_dataset(part).sample_count for part in result.parts) == 4
    assert taco.validate(result.path).ok


def test_single_partition_falls_back_to_one_archive(tmp_path: Path, collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "one.zip", partition_size="10GB") as writer:
        writer.add(make_sample(0))
        result = writer.run()
    assert not result.partitioned and result.path.name == "one.zip"


def test_partition_arguments(tmp_path: Path, collection) -> None:
    with pytest.raises(ValueError, match="not both"):
        taco.open_writer(collection, tmp_path / "x.zip", partition_size="1GB", partition_by="split")
    with pytest.raises(ValueError, match="collection-level"):
        taco.open_writer(collection, tmp_path / "x.zip", partition_by="nope")
    with pytest.raises(ValueError):
        taco.open_writer(collection, tmp_path / "x.zip", partition_size="lots")


def test_consolidate_manual_and_errors(tmp_path: Path, collection, make_sample) -> None:
    parts = []
    for name in ("a", "b"):
        with taco.open_writer(collection, tmp_path / f"{name}.zip") as writer:
            writer.add(make_sample(0 if name == "a" else 1))
            parts.append(writer.run().path)
    target = taco.consolidate(parts)
    assert target == tmp_path / ".tacocat"
    with pytest.raises(ConsolidationError, match="already exists"):
        taco.consolidate(parts)
    assert taco.consolidate(parts, overwrite=True) == target
    elsewhere = taco.consolidate(parts, tmp_path / "out", name="catalog")
    assert (elsewhere / "collection.parquet").is_file()

    other_contract = taco.Contract(structure=["x.tif"])
    other = collection.replace(contract=other_contract, id="other")
    with taco.open_writer(other, tmp_path / "c.zip") as writer:
        writer.add(taco.Sample(assets={"x.tif": b"x"}))
        different = writer.run().path
    with pytest.raises(ConsolidationError, match="different contract"):
        taco.consolidate([*parts, different], overwrite=True)
    with pytest.raises(ConsolidationError, match="no partitions"):
        taco.consolidate([])
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "a.zip").write_bytes(parts[0].read_bytes())
    with pytest.raises(ConsolidationError, match="unique"):
        taco.consolidate([parts[0], nested / "a.zip"], tmp_path / "dup")


def test_partition_name_collision(tmp_path: Path, make_sample) -> None:
    contract = taco.Contract(structure=["a.bin"], metadata={"collection": {"g": "string"}})
    collection = taco.Collection(
        contract=contract,
        id="g",
        dataset_version="0.1.0",
        description="d",
        licenses=["MIT"],
        providers=["p"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "g.zip", partition_by="g") as writer:
        writer.add(taco.Sample(assets={"a.bin": b"1"}, metadata={"collection": {"g": "a/b"}}))
        writer.add(taco.Sample(assets={"a.bin": b"2"}, metadata={"collection": {"g": "a:b"}}))
        with pytest.raises(WriterError, match="collide"):
            writer.run()
