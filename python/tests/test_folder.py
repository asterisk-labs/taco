from __future__ import annotations

from pathlib import Path

import pytest

import taco
from taco._view import open_view as open_dataset
from taco.errors import WriterError


def test_folder_writer_layout(tmp_path: Path, collection, make_sample) -> None:
    with taco.open_folder(collection, tmp_path / "ds") as writer:
        writer.add(make_sample(0, 1))
        writer.add(make_sample(1))
        result = writer.run()
    folder = result.path
    assert result.samples == 2 and result.data_files == 9 and result.size > 0
    assert (folder / "COLLECTION.json").is_file()
    assert sorted(item.name for item in (folder / "METADATA").iterdir()) == [
        "collection.parquet",
        "sample.parquet",
        "sample__after.parquet",
        "sample__before.parquet",
    ]
    assert (folder / "DATA" / "0" / "before" / "B02.tif").is_file()
    assert (folder / "DATA" / "0" / "extra0.png").is_file()
    assert not list(folder.glob(".taco-*"))
    dataset = open_dataset(folder)
    assert dataset.container == "folder"
    assert "internal:offset" not in dataset.level("sample").column_names
    assert dataset.collection.extent is None
    row = next(dataset.iter_data_rows())
    assert (folder / "DATA" / row.relative_path).is_file()
    assert taco.validate(folder).ok


def test_folder_append_and_link(tmp_path: Path, collection, make_sample) -> None:
    folder = tmp_path / "ds"
    with taco.open_folder(collection, folder, link=True) as writer:
        writer.add(make_sample(0))
        writer.run()
    with taco.open_folder(collection.replace(dataset_version="1.1.0"), folder, append=True) as writer:
        writer.add(make_sample(1, 2))
        result = writer.run()
    assert result.samples == 2 and result.data_files == 6
    dataset = open_dataset(folder)
    assert dataset.collection.dataset_version == "1.1.0"
    assert dataset.level("collection").column("internal:relative_path").to_pylist() == ["0", "1"]
    sample = dataset.level("sample").to_pylist()
    assert [row["internal:current_id"] for row in sample] == list(range(len(sample)))
    assert sample[-1]["internal:relative_path"] == "1/extra1.png"
    assert taco.validate(folder).ok

    other = taco.Contract(structure=["x.tif"])
    other_collection = collection.replace(contract=other)
    with taco.open_folder(other_collection, folder, append=True) as writer:
        writer.add(taco.Sample(assets={"x.tif": b"x"}, metadata={}))
        with pytest.raises(WriterError, match="different contract"):
            writer.run()
    assert open_dataset(folder).sample_count == 2


def test_folder_destination_rules(tmp_path: Path, collection, make_sample) -> None:
    with pytest.raises(WriterError):
        taco.open_folder(collection, tmp_path / "x.zip")
    with pytest.raises(ValueError):
        taco.open_folder(collection, tmp_path / "x", append=True, overwrite=True)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("keep")
    with taco.open_folder(collection, busy) as writer:
        writer.add(make_sample(0))
        with pytest.raises(FileExistsError):
            writer.run()
    with taco.open_folder(collection, busy, overwrite=True) as writer:
        writer.add(make_sample(0))
        with pytest.raises(WriterError, match="not a TACO folder"):
            writer.run()
    assert (busy / "notes.txt").read_text() == "keep"
    with taco.open_folder(collection, tmp_path / "missing", append=True) as writer:
        writer.add(make_sample(0))
        with pytest.raises(WriterError, match="append"):
            writer.run()


def test_folder_overwrite_keeps_previous_dataset_on_failure(
    tmp_path: Path, collection, make_sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "ds"
    with taco.open_folder(collection, folder) as writer:
        writer.add(make_sample(0))
        writer.run()

    original_collection = (folder / "COLLECTION.json").read_bytes()
    with taco.open_folder(collection.replace(dataset_version="2.0.0"), folder, overwrite=True) as writer:
        writer.add(make_sample(1))

        def fail_copy(source: Path, target: Path) -> int:
            raise OSError("copy failed")

        monkeypatch.setattr(writer, "_place", fail_copy)
        with pytest.raises(OSError, match="copy failed"):
            writer.run()

    assert (folder / "COLLECTION.json").read_bytes() == original_collection
    assert open_dataset(folder).sample_count == 1


def test_folder_append_restores_metadata_when_commit_fails(
    tmp_path: Path, collection, make_sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    import taco.writer.folder as folder_module

    folder = tmp_path / "ds"
    with taco.open_folder(collection, folder) as writer:
        writer.add(make_sample(0))
        writer.run()

    original_collection = (folder / "COLLECTION.json").read_bytes()
    native_replace = folder_module.os.replace
    failed = False

    def fail_once(source: str | Path, target: str | Path) -> None:
        nonlocal failed
        target_path = Path(target)
        if not failed and Path(source).name == "sample.parquet" and target_path.parent.name == "METADATA":
            failed = True
            raise OSError("commit failed")
        native_replace(source, target)

    monkeypatch.setattr(folder_module.os, "replace", fail_once)
    with taco.open_folder(collection.replace(dataset_version="2.0.0"), folder, append=True) as writer:
        writer.add(make_sample(1))
        with pytest.raises(OSError, match="commit failed"):
            writer.run()

    assert not (folder / "DATA" / "1").exists()
    assert (folder / "COLLECTION.json").read_bytes() == original_collection
    assert open_dataset(folder).sample_count == 1
