from __future__ import annotations

import runpy
from pathlib import Path

import pytest

import taco

EXAMPLES = (
    "numpy_minimal.py",
    "change_detection.py",
    "sequence.py",
    "time_series.py",
    "geospatial.py",
    "partitioned.py",
)


def test_minimal_example(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    example = Path(__file__).parents[1] / "examples" / "minimal.py"
    runpy.run_path(str(example))

    archive = tmp_path / "minimal.zip"
    assert archive.is_file()
    assert taco.validate(archive).ok
    dataset = taco.open_dataset(archive)
    assert dataset.collection.id == "minimal"
    assert dataset.contract.structure is None
    assert "sample file" in dataset._repr_html_()
    assert taco.read(dataset).num_rows == 1
    assert taco.read(dataset, layout="long").num_rows == 1


@pytest.mark.parametrize("name", EXAMPLES)
def test_example(name: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(Path(__file__).parents[1] / "examples" / name))
