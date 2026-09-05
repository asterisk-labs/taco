from __future__ import annotations

import os
import shutil
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

    for source, target in replacements:
        target.parent.mkdir(parents=True, exist_ok=True)
        publish_file(source, target, overwrite=overwrite)
