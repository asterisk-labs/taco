from __future__ import annotations

import errno
import json
import logging
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import taco
import taco.writer.folder as folder_module
from taco.container.publish import exchange
from taco.errors import WriterError
from taco.extensions.sample.split import Split
from taco.writer.folder import APPEND_DIR, FolderWriter, _Append

collection = taco.Collection(
    contract=taco.Contract(structure=["f/a.tif", "b.tif"], metadata=[taco.Level("sample", split=Split)]),
    id="killed",
    description="Appends cut short",
    licenses=["MIT"],
    providers=["me"],
)


def sample(name: str, split: str) -> taco.Sample:
    assets = [taco.Asset(f"{name}-a".encode(), path="f/a.tif"), taco.Asset(f"{name}-b".encode(), path="b.tif")]
    return taco.Sample(id=name, assets=assets, metadata=taco.Metadata(split=Split(split=split)))


# Simulate abrupt termination at each stage of an append.
CHILD = textwrap.dedent(
    """
    import os
    import shutil
    import signal
    import sys
    from pathlib import Path

    import taco
    import taco.writer.folder as folder
    from taco.extensions.sample.split import Split

    collection = taco.Collection(
        contract=taco.Contract(structure=["f/a.tif", "b.tif"], metadata=[taco.Level("sample", split=Split)]),
        id="killed",
        description="Appends cut short",
        licenses=["MIT"],
        providers=["me"],
    )
    dataset = Path(sys.argv[1]).resolve()
    point = sys.argv[2]
    switch = sys.argv[3]


    def kill_after(step):
        def run(*args, **kwargs):
            result = step(*args, **kwargs)
            target = Path(args[1])
            if (
                (point == "copy" and target.name == "b.tif")
                or (point == "data" and target.parent == dataset / "DATA")
                or (point == "aside" and target.name == "previous")
                or (point == "switch" and target == dataset / "METADATA")
                or (point == "collection" and target == dataset / "COLLECTION.json")
            ):
                os.kill(os.getpid(), signal.SIGKILL)
            return result

        return run


    def exchange_and_kill(first, second):
        if swap(first, second):
            os.kill(os.getpid(), signal.SIGKILL)
        return False


    shutil.copyfile = kill_after(shutil.copyfile)
    Path.rename = kill_after(Path.rename)
    Path.replace = kill_after(Path.replace)
    swap = folder.exchange
    if switch == "renames":
        folder.exchange = lambda first, second: False
    elif point == "exchange":
        folder.exchange = exchange_and_kill
    with taco.open_writer(collection, dataset, append=True) as writer:
        for name in ("b0", "b1"):
            assets = [taco.Asset(f"{name}-a".encode(), path="f/a.tif"), taco.Asset(f"{name}-b".encode(), path="b.tif")]
            writer.add(taco.Sample(id=name, assets=assets, metadata=taco.Metadata(split=Split(split="test"))))
        writer.run()
    """
)


def write(path: Path, *samples: taco.Sample, append: bool = False) -> None:
    with taco.open_writer(collection, path, append=append) as writer:
        writer.extend(samples)
        writer.run()


def ids(path: Path) -> list[str]:
    return taco.open_dataset(path).read().column("id").to_pylist()


def snapshot(path: Path) -> dict[str, bytes]:
    """Read published files, excluding append staging."""
    return {
        file.relative_to(path).as_posix(): file.read_bytes()
        for file in path.rglob("*")
        if file.is_file() and APPEND_DIR not in file.relative_to(path).parts
    }


def dataset_with_two_samples(tmp_path: Path) -> Path:
    path = tmp_path / "dataset"
    write(path, sample("a0", "train"), sample("a1", "validation"))
    return path.resolve()


def skip_without_exchange(directory: Path) -> None:
    first, second = directory / "first", directory / "second"
    first.mkdir()
    second.mkdir()
    try:
        if not exchange(first, second):
            pytest.skip("the file system cannot exchange two directories")
    finally:
        first.rmdir()
        second.rmdir()


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGKILL")
@pytest.mark.parametrize(
    ("point", "switch", "committed"),
    [
        ("copy", "exchange", False),
        ("data", "exchange", False),
        ("exchange", "exchange", True),
        ("collection", "exchange", True),
        ("aside", "renames", False),
        ("switch", "renames", True),
        ("collection", "renames", True),
    ],
)
def test_the_next_writer_repairs_a_killed_append(tmp_path: Path, point: str, switch: str, committed: bool) -> None:
    if point == "exchange":
        skip_without_exchange(tmp_path)
    path = dataset_with_two_samples(tmp_path)
    before = snapshot(path)

    killed = subprocess.run([sys.executable, "-c", CHILD, str(path), point, switch], capture_output=True, text=True)
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    assert (path / APPEND_DIR / "lock").is_file()
    if point == "copy":
        assert snapshot(path) == before
        assert taco.validate(path).ok
    elif point == "data":
        # Unlisted DATA/ samples remain invisible to readers.
        assert sorted(entry.name for entry in (path / "DATA").iterdir()) == ["0", "1", "2"]
        assert ids(path) == ["a0", "a1"]
    elif point == "aside":
        assert not (path / "METADATA").exists()
    elif point in ("switch", "exchange"):
        # Metadata is committed, but the old collection summary remains.
        assert ids(path) == ["a0", "a1", "b0", "b1"]
        assert not taco.validate(path).ok

    with _Append(path):
        pass
    assert not (path / APPEND_DIR).exists()
    expected = ["a0", "a1", "b0", "b1"] if committed else ["a0", "a1"]
    assert ids(path) == expected
    assert taco.validate(path).ok
    if not committed:
        assert snapshot(path) == before
    assert json.loads((path / "COLLECTION.json").read_text())["split:counts"]["test"] == (2 if committed else 0)

    write(path, sample("c0", "train"), append=True)
    assert ids(path) == [*expected, "c0"]
    assert taco.validate(path).ok
    assert not (path / APPEND_DIR).exists()


def test_append_stages_files_outside_published_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = dataset_with_two_samples(tmp_path)
    before = snapshot(path)
    copy = FolderWriter._copy_asset
    targets: list[Path] = []

    def recording(self: FolderWriter, source: Path, target: Path) -> int:
        assert snapshot(path) == before
        targets.append(target)
        return copy(self, source, target)

    monkeypatch.setattr(FolderWriter, "_copy_asset", recording)
    write(path, sample("b0", "test"), append=True)
    assert targets
    assert all(path / APPEND_DIR / "DATA" in target.parents for target in targets)
    assert ids(path) == ["a0", "a1", "b0"]
    assert not (path / APPEND_DIR).exists()


@pytest.mark.parametrize(
    ("step", "switch"),
    [
        ("data", "exchange"),
        ("exchange", "exchange"),
        ("collection", "exchange"),
        ("aside", "renames"),
        ("switch", "renames"),
        ("collection", "renames"),
    ],
)
def test_a_failed_commit_leaves_the_dataset_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str, switch: str
) -> None:
    path = dataset_with_two_samples(tmp_path)
    before = snapshot(path)
    rename, replace = Path.rename, Path.replace

    def check(source: Path, target: Path) -> None:
        if (
            (step == "data" and target == path / "DATA" / "3")
            or (step == "aside" and target.name == "previous")
            or (step == "switch" and source == path / APPEND_DIR / "METADATA" and target == path / "METADATA")
            or (step == "collection" and target == path / "COLLECTION.json")
        ):
            raise OSError("disk full")

    def failing_rename(self: Path, target: str | Path) -> Path:
        check(self, Path(target))
        return rename(self, target)

    def failing_replace(self: Path, target: str | Path) -> Path:
        check(self, Path(target))
        return replace(self, target)

    def failing_exchange(first: Path, second: Path) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "rename", failing_rename)
    monkeypatch.setattr(Path, "replace", failing_replace)
    if switch == "renames":
        monkeypatch.setattr(folder_module, "exchange", lambda first, second: False)
    elif step == "exchange":
        monkeypatch.setattr(folder_module, "exchange", failing_exchange)
    with taco.open_writer(collection, path, append=True) as writer:
        writer.extend([sample("b0", "test"), sample("b1", "test")])
        with pytest.raises(OSError, match="disk full"):
            writer.run()
    monkeypatch.undo()

    assert snapshot(path) == before
    assert sorted(entry.name for entry in path.iterdir()) == ["COLLECTION.json", "DATA", "METADATA"]
    write(path, sample("c0", "test"), append=True)
    assert ids(path) == ["a0", "a1", "c0"]


def test_append_switches_metadata_in_one_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skip_without_exchange(tmp_path)
    path = dataset_with_two_samples(tmp_path)
    rename = Path.rename

    def guarded(self: Path, target: str | Path) -> Path:
        assert self != path / "METADATA", "readers would find no METADATA/"
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", guarded)
    write(path, sample("b0", "test"), append=True)
    assert ids(path) == ["a0", "a1", "b0"]
    assert taco.validate(path).ok
    assert not (path / APPEND_DIR).exists()


def test_the_next_writer_completes_an_exchange_it_could_not_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_without_exchange(tmp_path)
    path = dataset_with_two_samples(tmp_path)
    calls: list[Path] = []

    def exchange_once(first: Path, second: Path) -> bool:
        calls.append(first)
        return len(calls) == 1 and exchange(first, second)

    def failing_publish(source: Path, target: Path, *, overwrite: bool) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch, taco.open_writer(collection, path, append=True) as writer:
        patch.setattr(folder_module, "exchange", exchange_once)
        patch.setattr(folder_module, "publish_file", failing_publish)
        writer.add(sample("b0", "test"))
        with pytest.raises(WriterError, match="could not switch"):
            writer.run()

    # The new metadata stays published and its collection waits in staging.
    assert len(calls) == 2
    assert ids(path) == ["a0", "a1", "b0"]
    assert (path / APPEND_DIR / "COLLECTION.json").is_file()
    write(path, sample("c0", "train"), append=True)
    assert ids(path) == ["a0", "a1", "b0", "c0"]
    assert taco.validate(path).ok
    assert not (path / APPEND_DIR).exists()


def test_an_interrupt_after_the_commit_keeps_the_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = dataset_with_two_samples(tmp_path)
    publish = folder_module.publish_file

    def interrupted(*args: object, **kwargs: object) -> None:
        publish(*args, **kwargs)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    monkeypatch.setattr(folder_module, "publish_file", interrupted)
    with taco.open_writer(collection, path, append=True) as writer:
        writer.add(sample("b0", "test"))
        with pytest.raises(KeyboardInterrupt):
            writer.run()
    monkeypatch.undo()

    assert ids(path) == ["a0", "a1", "b0"]
    assert taco.validate(path).ok
    assert not (path / APPEND_DIR).exists()


def test_a_running_append_blocks_another(tmp_path: Path) -> None:
    path = dataset_with_two_samples(tmp_path)
    before = snapshot(path)
    with _Append(path):
        with taco.open_writer(collection, path, append=True) as writer:
            writer.add(sample("b0", "test"))
            with pytest.raises(WriterError, match="another writer is appending"):
                writer.run()
        assert (path / APPEND_DIR / "lock").is_file()
    assert snapshot(path) == before
    assert not (path / APPEND_DIR).exists()


def test_append_without_file_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = dataset_with_two_samples(tmp_path)

    def unsupported(descriptor: int) -> bool:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(folder_module, "_try_lock", unsupported)
    with caplog.at_level(logging.WARNING, logger="taco"):
        write(path, sample("b0", "test"), append=True)
    assert "nothing stops another append" in caplog.text
    assert ids(path) == ["a0", "a1", "b0"]
    assert not (path / APPEND_DIR).exists()


def test_append_removes_samples_the_metadata_does_not_describe(tmp_path: Path) -> None:
    # Such folders were left by writers that appended straight into DATA/.
    path = dataset_with_two_samples(tmp_path)
    (path / "DATA" / "2").mkdir()
    (path / "DATA" / "2" / "b.tif").write_bytes(b"stale")
    (path / "DATA" / "7").mkdir()
    (path / "DATA" / "02").mkdir()
    assert not taco.validate(path).ok

    write(path, sample("b0", "test"), append=True)
    assert sorted(entry.name for entry in (path / "DATA").iterdir()) == ["0", "02", "1", "2"]
    assert (path / "DATA" / "2" / "b.tif").read_bytes() == b"b0-b"
    assert ids(path) == ["a0", "a1", "b0"]


def test_append_needs_a_folder_dataset(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with taco.open_writer(collection, plain, append=True) as writer:
        writer.add(sample("b0", "test"))
        with pytest.raises(WriterError, match="existing FOLDER"):
            writer.run()
    assert list(plain.iterdir()) == []
