from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def _move_without_replacing(source: Path, target: Path) -> None:
    if source.is_dir() or os.name == "nt":
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        source.rename(target)
        return

    target.hardlink_to(source)
    source.unlink()


def publish_file(source: Path, target: Path, *, overwrite: bool) -> None:
    if overwrite:
        if source.is_dir() and (target.exists() or target.is_symlink()):
            _remove(target)
        source.replace(target)
        return
    try:
        _move_without_replacing(source, target)
    except FileExistsError as exc:
        raise FileExistsError(f"output already exists (set overwrite=True): {target}") from exc


def publish_many(replacements: Sequence[tuple[Path, Path]], *, overwrite: bool) -> None:
    if not replacements:
        return
    if len({target for _, target in replacements}) != len(replacements):
        raise ValueError("a publication target appears more than once")
    if not overwrite:
        existing = next(
            (target for _, target in replacements if target.exists() or target.is_symlink()),
            None,
        )
        if existing is not None:
            raise FileExistsError(f"output already exists (set overwrite=True): {existing}")

    parent = replacements[0][1].parent
    parent.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=".taco-backup-", dir=parent))
    (backup / "new").mkdir()
    previous: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        if overwrite:
            for index, (_, target) in enumerate(replacements):
                if target.exists() or target.is_symlink():
                    saved = backup / f"old-{index}"
                    target.replace(saved)
                    previous.append((saved, target))
        for source, target in replacements:
            target.parent.mkdir(parents=True, exist_ok=True)
            _move_without_replacing(source, target)
            installed.append(target)
    except BaseException as exc:
        failures = _restore(previous, installed, backup)
        if failures:
            details = "; ".join(failures)
            raise RuntimeError(f"publication failed and could not be restored: {details}; backup: {backup}") from exc
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _restore(previous: list[tuple[Path, Path]], installed: list[Path], backup: Path) -> list[str]:
    move_failures: dict[Path, str] = {}
    restore_failures: list[str] = []
    discarded = backup / "new"
    for index, target in reversed(list(enumerate(installed))):
        if not target.exists() and not target.is_symlink():
            continue
        try:
            target.replace(discarded / str(index))
        except OSError as exc:
            move_failures[target] = f"could not move {target}: {exc}"
    for saved, target in reversed(previous):
        try:
            saved.replace(target)
            move_failures.pop(target, None)
        except OSError as exc:
            restore_failures.append(f"could not restore {target}: {exc}")
    return [*move_failures.values(), *restore_failures]
