from __future__ import annotations

from pathlib import Path

import pytest

from taco import _publish


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
