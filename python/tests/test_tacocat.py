from __future__ import annotations

import json
from pathlib import Path

import pytest

import taco
from taco.container.view import open_view
from taco.errors import ConsolidationError, ContainerError


def build(path: Path, collection: taco.Collection, samples: list[taco.Sample]) -> Path:
    with taco.open_writer(collection, path) as writer:
        writer.extend(samples)
        writer.run()
    return path


def test_consolidate(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    parts = [
        build(tmp_path / "a.zip", collection, [make_sample(0), make_sample(1)]),
        build(tmp_path / "b.zip", collection, [make_sample(2)]),
    ]
    output = taco.consolidate(parts)
    dataset = open_view(output)
    assert dataset.container == "tacocat"
    assert dataset.sample_count == 3
    assert dataset.collection.sources == {
        "samples": 3,
        "partitions": [
            {
                "file": "a.zip",
                "samples": 2,
                "spatial": [-76.125, -12.125, -74.875, -11.75],
                "temporal": ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
            },
            {
                "file": "b.zip",
                "samples": 1,
                "spatial": [-74.125, -11.875, -73.875, -11.625],
                "temporal": ["2024-01-03T00:00:00Z", "2024-01-03T00:00:00Z"],
            },
        ],
    }
    assert dataset.collection.extent == taco.contract.Extent(
        (-76.125, -12.125, -73.875, -11.625),
        ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
    )
    table = dataset.level("sample")
    assert table.column("internal:current_id").to_pylist() == [0, 1, 2]
    assert table.column("internal:source_file").to_pylist() == ["a.zip", "a.zip", "b.zip"]
    assert taco.validate(output).ok

    opened = taco.open_dataset(output)
    assert opened.collection.sources == dataset.collection.sources
    assert ">TACOCAT<" in opened._repr_html_()
    assert opened.read().num_rows == 3
    assert set(opened.read().column("source_file").to_pylist()) == {"a.zip", "b.zip"}
    assert opened.sql('SELECT * FROM dataset ORDER BY "taco:sample_index"').equals(opened.read())
    with pytest.raises(ContainerError, match="TACOCAT"):
        taco.open_dataset([output, parts[0]])


def test_open_partitions(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    parts = [
        build(tmp_path / "a.zip", collection, [make_sample(0), make_sample(1)]),
        build(tmp_path / "b.zip", collection, [make_sample(2)]),
    ]

    dataset = taco.open_dataset(parts)
    wide = dataset.read()

    assert dataset.sources == tuple(parts)
    assert dataset.collection.extent == taco.contract.Extent(
        (-76.125, -12.125, -73.875, -11.625),
        ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
    )
    assert ">PARTITIONS<" in dataset._repr_html_()
    assert wide.num_rows == 3
    assert set(
        zip(wide.column("source_file").to_pylist(), wide.column("taco:sample_index").to_pylist(), strict=True)
    ) == {
        ("a.zip", 0),
        ("a.zip", 1),
        ("b.zip", 2),
    }
    assert dataset.sql('SELECT * FROM dataset WHERE "taco:sample_index" = 0').num_rows == 1
    assert dataset.sql('SELECT * FROM dataset ORDER BY "taco:sample_index"').equals(wide)
    raw = dataset.sql("SELECT * FROM sample")
    assert raw.num_rows == 3
    assert set(raw.column("source_file").to_pylist()) == {"a.zip", "b.zip"}
    for row in wide.to_pylist():
        assert str(tmp_path / row["source_file"]) in row["before__B02.tif::location"]


def test_consolidate_preserves_schema_metadata(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    parts = [
        build(tmp_path / "a.zip", collection, [make_sample(0)]),
        build(tmp_path / "b.zip", collection, [make_sample(1)]),
    ]
    table = open_view(taco.consolidate(parts)).level("sample")
    assert table.schema.metadata == {b"taco:level": b"sample"}
    assert table.schema.field("ml:split").metadata == {b"description": b"Dataset split"}


def test_consolidate_rejects_contract_mismatch(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    first = build(tmp_path / "a.zip", collection, [make_sample(0)])
    changed = collection.replace(contract=taco.Contract(structure=["a.bin"]))
    second = build(tmp_path / "b.zip", changed, [taco.Sample(id="u25", assets=[taco.Asset(b"x", path="a.bin")])])
    with pytest.raises(ContainerError, match="same collection"):
        taco.open_dataset([first, second])
    with pytest.raises(ConsolidationError, match="contract"):
        taco.consolidate([first, second])


def test_consolidate_rejects_collection_metadata_mismatch(
    tmp_path: Path, collection: taco.Collection, make_sample
) -> None:
    first = build(tmp_path / "a.zip", collection, [make_sample(0)])
    changed = collection.replace(labels=taco.metadata.collection.Labels(classes=["clear"]))
    second = build(tmp_path / "b.zip", changed, [make_sample(1)])
    with pytest.raises(ContainerError, match="same collection"):
        taco.open_dataset([first, second])
    with pytest.raises(ConsolidationError, match="collection metadata"):
        taco.consolidate([first, second])


def test_consolidate_rejects_duplicate_sample_ids(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    first = build(tmp_path / "a.zip", collection, [make_sample(0)])
    second = build(tmp_path / "b.zip", collection, [make_sample(0)])

    with pytest.raises(ConsolidationError, match="duplicate sample id"):
        taco.consolidate([first, second])


def test_sources_shape_is_validated(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    parts = [
        build(tmp_path / "a.zip", collection, [make_sample(0)]),
        build(tmp_path / "b.zip", collection, [make_sample(1)]),
    ]
    output = taco.consolidate(parts)
    file = output / "COLLECTION.json"
    data = json.loads(file.read_text())
    data["taco:sources"]["samples"] = 99
    file.write_text(json.dumps(data))
    report = taco.validate(output)
    assert not report.ok
    assert any(issue.code == "sources" for issue in report.errors)


@pytest.mark.parametrize("source", ["../a.zip", "/tmp/a.zip", "parts\\a.zip", "parts/a", "parts/./a.zip"])
def test_source_paths_must_be_normalized_zip_paths(
    source: str, tmp_path: Path, collection: taco.Collection, make_sample
) -> None:
    part = build(tmp_path / "a.zip", collection, [make_sample(0)])
    output = taco.consolidate([part])
    file = output / "COLLECTION.json"
    data = json.loads(file.read_text())
    data["taco:sources"]["partitions"][0]["file"] = source
    file.write_text(json.dumps(data))

    report = taco.validate(output)
    assert not report.ok
    assert any(issue.code == "sources" for issue in report.errors)
