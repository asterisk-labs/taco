from __future__ import annotations

import ctypes
import errno
import sys
from pathlib import Path

import pytest

from taco.container import publish as _publish


def no_hard_links(self: Path, target: Path) -> None:
    raise OSError(errno.EPERM, "Operation not permitted", str(self))


@pytest.mark.parametrize("atomic", [True, False])
def test_publish_without_hard_links_keeps_an_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, atomic: bool
) -> None:
    monkeypatch.setattr(Path, "hardlink_to", no_hard_links)
    if not atomic:
        monkeypatch.setattr(_publish, "_rename", lambda source, target, flags: False)
    source = tmp_path / "new"
    target = tmp_path / "target"
    source.write_text("first")
    _publish.publish_file(source, target, overwrite=False)
    assert target.read_text() == "first"
    assert not source.exists()

    source.write_text("second")
    with pytest.raises(FileExistsError, match="output already exists"):
        _publish.publish_file(source, target, overwrite=False)
    assert target.read_text() == "first"
    assert source.read_text() == "second"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows publishes by rename")
def test_publish_raises_other_hard_link_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(self: Path, target: Path) -> None:
        raise OSError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(Path, "hardlink_to", denied)
    source = tmp_path / "new"
    source.write_text("new")
    with pytest.raises(PermissionError):
        _publish.publish_file(source, tmp_path / "target", overwrite=False)
    assert source.read_text() == "new"
    assert not (tmp_path / "target").exists()


def test_exchange_swaps_two_directories(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "a").mkdir(parents=True)
    (second / "b").mkdir(parents=True)
    if not _publish.exchange(first, second):
        pytest.skip("the file system cannot exchange two directories")
    assert [entry.name for entry in first.iterdir()] == ["b"]
    assert [entry.name for entry in second.iterdir()] == ["a"]
    with pytest.raises(FileNotFoundError):
        _publish.exchange(tmp_path / "missing", first)


def test_exchange_is_false_where_the_file_system_cannot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unsupported(source: bytes, target: bytes, flags: int) -> int:
        ctypes.set_errno(errno.EINVAL)
        return -1

    monkeypatch.setattr(_publish, "_rename_function", lambda: unsupported)
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "a").mkdir(parents=True)
    second.mkdir()
    assert not _publish.exchange(first, second)
    assert [entry.name for entry in first.iterdir()] == ["a"]
    assert list(second.iterdir()) == []


def test_publish_many_restores_existing_outputs_on_failure(tmp_path: Path, monkeypatch) -> None:
    first_source = tmp_path / "first.new"
    second_source = tmp_path / "second.new"
    first_target = tmp_path / "first"
    second_target = tmp_path / "second"
    first_source.write_text("new first")
    second_source.write_text("new second")
    first_target.write_text("old first")
    second_target.write_text("old second")

    move = _publish._move_without_replacing
    replace = Path.replace

    def fail_publish(source: Path, target: Path) -> None:
        if source == second_source:
            raise OSError("publish failed")
        move(source, target)

    def fail_discard(path: Path, target: Path) -> Path:
        if path == first_target and target.parent.name == "new":
            raise OSError("discard failed")
        return replace(path, target)

    monkeypatch.setattr(_publish, "_move_without_replacing", fail_publish)
    monkeypatch.setattr(Path, "replace", fail_discard)

    with pytest.raises(OSError, match="publish failed"):
        _publish.publish_many(
            [(first_source, first_target), (second_source, second_target)],
            overwrite=True,
        )

    assert first_target.read_text() == "old first"
    assert second_target.read_text() == "old second"
    assert not list(tmp_path.glob(".taco-backup-*"))


def test_publish_many_keeps_backup_when_restore_fails(tmp_path: Path, monkeypatch) -> None:
    first_source = tmp_path / "first.new"
    second_source = tmp_path / "second.new"
    first_target = tmp_path / "first"
    second_target = tmp_path / "second"
    first_source.write_text("new first")
    second_source.write_text("new second")
    first_target.write_text("old first")
    second_target.write_text("old second")

    move = _publish._move_without_replacing
    replace = Path.replace

    def fail_publish(source: Path, target: Path) -> None:
        if source == second_source:
            raise OSError("publish failed")
        move(source, target)

    def fail_restore(path: Path, target: Path) -> Path:
        if path.name == "old-0" and target == first_target:
            raise OSError("restore failed")
        return replace(path, target)

    monkeypatch.setattr(_publish, "_move_without_replacing", fail_publish)
    monkeypatch.setattr(Path, "replace", fail_restore)

    with pytest.raises(RuntimeError, match="backup"):
        _publish.publish_many(
            [(first_source, first_target), (second_source, second_target)],
            overwrite=True,
        )

    backups = list(tmp_path.glob(".taco-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "old-0").read_text() == "old first"
    assert second_target.read_text() == "old second"


def test_publish_many_rejects_duplicate_targets(tmp_path: Path) -> None:
    target = tmp_path / "target"
    with pytest.raises(ValueError, match="more than once"):
        _publish.publish_many([(tmp_path / "a", target), (tmp_path / "b", target)], overwrite=True)
