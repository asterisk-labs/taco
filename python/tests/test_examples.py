from __future__ import annotations

import runpy
from pathlib import Path

import taco


def test_minimal_example(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    example = Path(__file__).parents[1] / "examples" / "minimal.py"
    runpy.run_path(str(example))

    archive = tmp_path / "minimal.zip"
    assert archive.is_file()
    assert taco.validate(archive).ok
