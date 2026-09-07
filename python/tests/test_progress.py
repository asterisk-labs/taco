from __future__ import annotations

from types import SimpleNamespace

import pytest

import taco
from taco.writer import _progress


class _Bar:
    def __init__(self, options: dict[str, object]) -> None:
        self.options = options
        self.updates = 0
        self.closed = False

    def update(self, value: int = 1) -> None:
        self.updates += value

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("name", "options", "samples", "descriptions"),
    [
        ("data.zip", {}, 1, ["planning data.zip", "metadata data.zip", "packing data.zip"]),
        ("data", {}, 1, ["writing data"]),
        (
            "parts.zip",
            {"partition_size": 1},
            2,
            [
                "planning parts_part0001.zip",
                "metadata parts_part0001.zip",
                "packing parts_part0001.zip",
                "planning parts_part0002.zip",
                "metadata parts_part0002.zip",
                "packing parts_part0002.zip",
            ],
        ),
    ],
)
def test_writer_progress(name, options, samples, descriptions, tmp_path, collection, make_sample, monkeypatch) -> None:
    bars: list[_Bar] = []

    def tqdm(**options: object) -> _Bar:
        bar = _Bar(options)
        bars.append(bar)
        return bar

    monkeypatch.setattr(_progress, "import_module", lambda _: SimpleNamespace(tqdm=tqdm))
    with taco.open_writer(collection, tmp_path / name, progress=True, **options) as writer:
        writer.extend(make_sample(index) for index in range(samples))
        writer.run()

    assert [bar.options["desc"] for bar in bars] == descriptions
    assert [bar.updates for bar in bars] == [1] * len(bars)
    assert all(bar.closed for bar in bars)


def test_progress_requires_tqdm(monkeypatch) -> None:
    def missing(_: str):
        raise ModuleNotFoundError(name="tqdm")

    monkeypatch.setattr(_progress, "import_module", missing)
    with pytest.raises(ImportError, match=r"taco-eo\[progress\]"):
        _progress.Progress(True, 1, "writing")
