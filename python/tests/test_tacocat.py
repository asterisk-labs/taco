from __future__ import annotations

import json
from pathlib import Path

import pytest

import taco
from taco._view import open_view
from taco.errors import ConsolidationError


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
        "partitions": [{"file": "a.zip", "samples": 2}, {"file": "b.zip", "samples": 1}],
    }
    table = dataset.level("sample")
    assert table.column("internal:current_id").to_pylist() == [0, 1, 0]
    assert table.column("internal:source_file").to_pylist() == ["a.zip", "a.zip", "b.zip"]
    assert taco.validate(output).ok


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
