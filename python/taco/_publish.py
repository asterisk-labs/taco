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

    os.link(source, target)
    source.unlink()


def publish_file(source: Path, target: Path, *, overwrite: bool) -> None:
    if overwrite:
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
    with tempfile.TemporaryDirectory(prefix=".taco-backup-", dir=parent, ignore_cleanup_errors=True) as name:
        backup = Path(name)
        previous: list[tuple[Path, Path]] = []
        installed: list[Path] = []
        try:
            if overwrite:
                for index, (_, target) in enumerate(replacements):
                    if target.exists() or target.is_symlink():
                        saved = backup / str(index)
                        target.replace(saved)
                        previous.append((saved, target))
            for source, target in replacements:
                target.parent.mkdir(parents=True, exist_ok=True)
                _move_without_replacing(source, target)
                installed.append(target)
        except BaseException:
            for target in reversed(installed):
                _remove(target)
            for saved, target in reversed(previous):
                saved.replace(target)
            raise
