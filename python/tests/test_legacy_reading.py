"""Collections written before taco 0.14 still read.

They carry `dataset_version` in COLLECTION.json and no `id` column in
sample.parquet. Reading drops the first and derives the second from the row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import taco


def _as_legacy(folder: Path) -> None:
    """Rewrite a FOLDER the way taco wrote it before 0.14."""
    collection = folder / "COLLECTION.json"
    document = json.loads(collection.read_text())
    document["dataset_version"] = "1.0.0"
    collection.write_text(json.dumps(document))
    sample = folder / "METADATA" / "sample.parquet"
    table = pq.read_table(sample)
    pq.write_table(table.drop_columns(["id"]), sample)


def test_a_legacy_folder_reads_with_ids_from_its_rows(folder_dataset: Path) -> None:
    _as_legacy(folder_dataset)
    dataset = taco.open_dataset(folder_dataset)

    assert "dataset_version" not in dataset.collection.to_dict()
    table = dataset.read()
    assert table.num_rows == 4
    assert table.column("id").to_pylist() == ["0", "1", "2", "3"]
    assert dataset.sql("SELECT id FROM sample ORDER BY id").column("id").to_pylist() == ["0", "1", "2", "3"]


def test_a_current_folder_keeps_its_own_ids(folder_dataset: Path) -> None:
    before = taco.open_dataset(folder_dataset).read().column("id").to_pylist()
    assert before
    assert all(not value.isdigit() for value in before)


def test_a_legacy_folder_does_not_validate(folder_dataset: Path) -> None:
    # Reading tolerates the old layout; validation still reports it.
    _as_legacy(folder_dataset)
    pytest.importorskip("cozip")
    report = taco.validate(folder_dataset)
    assert not report.ok


def test_a_legacy_sequence_may_hold_no_file(folder_dataset: Path) -> None:
    _as_legacy(folder_dataset)
    collection = folder_dataset / "COLLECTION.json"
    document = json.loads(collection.read_text())
    variable = next(item for item in document["taco:structure"] if "*[" in item)
    relaxed = variable.replace("*[1,", "*[0,")
    document["taco:structure"] = [relaxed if item == variable else item for item in document["taco:structure"]]
    collection.write_text(json.dumps(document))

    assert taco.open_dataset(folder_dataset).read().num_rows == 4
    document.pop("dataset_version")
    collection.write_text(json.dumps(document))
    with pytest.raises(taco.TacoError, match="at least one file"):
        taco.open_dataset(folder_dataset)
