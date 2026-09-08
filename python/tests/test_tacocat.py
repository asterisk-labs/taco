from __future__ import annotations

import json
from pathlib import Path

import pytest

import taco
from taco._view import open_view
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
                "spatial": [-76.0, -12.0, -75.0, -11.9],
                "temporal": ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
            },
            {
                "file": "b.zip",
                "samples": 1,
                "spatial": [-74.0, -11.8, -74.0, -11.8],
                "temporal": ["2024-01-03T00:00:00Z", "2024-01-03T00:00:00Z"],
            },
        ],
    }
    assert dataset.collection.extent == taco.contract.Extent(
        (-76, -12, -74, -11.8),
        ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
    )
    table = dataset.level("sample")
    assert table.column("internal:current_id").to_pylist() == [0, 1, 0]
    assert table.column("internal:source_file").to_pylist() == ["a.zip", "a.zip", "b.zip"]
    assert taco.validate(output).ok

    opened = taco.open_dataset(output)
    assert opened.collection.sources == dataset.collection.sources
    assert ">TACOCAT<" in opened._repr_html_()
    assert taco.read(opened).num_rows == 3
    assert set(taco.read(opened).column("source_file").to_pylist()) == {"a.zip", "b.zip"}
    assert taco.read(opened, layout="long").num_rows == 12
    with pytest.raises(ContainerError, match="TACOCAT"):
        taco.open_dataset([output, parts[0]])


def test_open_partitions(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    parts = [
        build(tmp_path / "a.zip", collection, [make_sample(0), make_sample(1)]),
        build(tmp_path / "b.zip", collection, [make_sample(2)]),
    ]

    dataset = taco.open_dataset(parts)
    wide = taco.read(dataset)

    assert dataset.sources == tuple(parts)
    assert dataset.collection.extent == taco.contract.Extent(
        (-76, -12, -74, -11.8),
        ("2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z"),
    )
    assert ">PARTITIONS<" in dataset._repr_html_()
    assert wide.num_rows == 3
    assert set(zip(wide.column("source_file").to_pylist(), wide.column("sample_id").to_pylist(), strict=True)) == {
        ("a.zip", 0),
        ("a.zip", 1),
        ("b.zip", 0),
    }
    assert taco.read(dataset, idx=0).num_rows == 2
    assert taco.read(dataset, layout="long").num_rows == 12
    raw = taco.read(dataset, level="sample")
    assert raw.num_rows == 3
    assert set(raw.column("source_file").to_pylist()) == {"a.zip", "b.zip"}
    for row in wide.to_pylist():
        assert str(tmp_path / row["source_file"]) in row["before/B02.tif"]


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
    second = build(tmp_path / "b.zip", changed, [taco.Sample(assets=[taco.Asset(b"x", path="a.bin")])])
    with pytest.raises(ContainerError, match="same collection"):
        taco.open_dataset([first, second])
    with pytest.raises(ConsolidationError, match="contract"):
        taco.consolidate([first, second])


def test_consolidate_rejects_collection_metadata_mismatch(
    tmp_path: Path, collection: taco.Collection, make_sample
) -> None:
    first = build(tmp_path / "a.zip", collection, [make_sample(0)])
    changed = collection.replace(
        metadata=taco.CollectionMetadata(labels=taco.metadata.collection.Labels(classes=["clear"]))
    )
    second = build(tmp_path / "b.zip", changed, [make_sample(1)])
    with pytest.raises(ContainerError, match="same collection"):
        taco.open_dataset([first, second])
    with pytest.raises(ConsolidationError, match="collection metadata"):
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
