from __future__ import annotations

import ctypes
import errno
import functools
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

# Flags of renameat2 on Linux and renamex_np on macOS.
_NO_REPLACE = 1 if sys.platform == "linux" else 4  # RENAME_NOREPLACE, RENAME_EXCL
_EXCHANGE = 2  # RENAME_EXCHANGE, RENAME_SWAP
_AT_FDCWD = -100
# Errors that mean the file system has no hard links or does not know a rename flag.
_UNSUPPORTED = frozenset({errno.EINVAL, errno.EMLINK, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM})


@functools.cache
def _rename_function() -> Callable[[bytes, bytes, int], int] | None:
    """renameat2 on Linux or renamex_np on macOS, if the C library has it."""
    if sys.platform not in ("linux", "darwin"):
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renamex_np if sys.platform == "darwin" else libc.renameat2
    except (AttributeError, OSError):
        return None
    function.restype = ctypes.c_int
    if sys.platform == "darwin":
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        return function
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    return lambda source, target, flags: function(_AT_FDCWD, source, _AT_FDCWD, target, flags)


def _rename(source: Path, target: Path, flags: int) -> bool:
    """Rename with a renameat2 or renamex_np flag; False where it is not supported."""
    function = _rename_function()
    if function is None:
        return False
    if function(os.fsencode(source), os.fsencode(target), flags) == 0:
        return True
    error = ctypes.get_errno()
    if error in _UNSUPPORTED:
        return False
    raise OSError(error, os.strerror(error), os.fspath(source), None, os.fspath(target))


def exchange(first: Path, second: Path) -> bool:
    """Swap two paths in one step; False where the OS or file system cannot."""
    return _rename(first, second, _EXCHANGE)


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def _rename_without_replacing(source: Path, target: Path) -> None:
    if _rename(source, target, _NO_REPLACE):
        return
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    # On POSIX this replaces a target created after the check.
    source.rename(target)


def _move_without_replacing(source: Path, target: Path) -> None:
    if source.is_dir() or os.name == "nt":
        _rename_without_replacing(source, target)
        return

    try:
        target.hardlink_to(source)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED:
            raise
        # exFAT, SMB and some FUSE mounts have no hard links.
        _rename_without_replacing(source, target)
        return
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
