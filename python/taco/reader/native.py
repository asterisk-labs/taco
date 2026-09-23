"""The native TACO core, shared with the R and Julia packages."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Sequence
from os import PathLike, fspath
from pathlib import Path
from typing import Any

from cffi import FFI

from ..errors import ContainerError

API_VERSION = 2
LIBRARY_ENV = "TACO_LIB"

_ffi = FFI()
_ffi.cdef(
    """
    typedef enum {
        TACO_OK = 0,
        TACO_ERR_INVALID = 1,
        TACO_ERR_IO = 2,
        TACO_ERR_NOT_FOUND = 3,
        TACO_ERR_INTERNAL = 99
    } taco_status;

    int taco_api_version(void);
    const char* taco_version_string(void);
    const char* taco_last_error(void);
    void taco_free(char* text);

    typedef void (*taco_progress_fn)(const char* phase, uint64_t done, uint64_t total, void* user);
    void taco_set_progress(taco_progress_fn callback, void* user);

    typedef struct taco_dataset taco_dataset;
    taco_status taco_open(const char* source, const char* cache_dir, taco_dataset** out);
    void taco_close(taco_dataset* dataset);
    const char* taco_dataset_source(const taco_dataset* dataset);
    const char* taco_dataset_container(const taco_dataset* dataset);
    const char* taco_dataset_collection(const taco_dataset* dataset);
    size_t taco_dataset_level_count(const taco_dataset* dataset);
    const char* taco_dataset_level(const taco_dataset* dataset, size_t index);
    size_t taco_dataset_structure_count(const taco_dataset* dataset);
    const char* taco_dataset_structure(const taco_dataset* dataset, size_t index);
    const char* taco_dataset_derived(const taco_dataset* dataset);

    typedef struct {
        const char* idx;
        const char* level;
        int pivoted;
        const char* const* files;
        size_t file_count;
        int location;
    } taco_read_options;

    taco_status taco_sql(const taco_dataset* const* datasets, size_t count,
                         const taco_read_options* options, char** out_sql);
    taco_status taco_profile(const char* source, char** out_name);
    typedef struct {
        const char* uri;
        uint64_t offset;
        uint64_t length;
        const char* path;
    } taco_fetch_item;

    taco_status taco_fetch(const taco_fetch_item* items, size_t count);
    """
)

_lock = threading.Lock()
_library: Any = None
_bars: dict[str, Any] = {}


def _on_progress(phase: Any, done: int, total: int, user: Any) -> None:
    # C callbacks must not raise. Bars are keyed by native phase.
    try:
        name = _ffi.string(phase).decode("utf-8", errors="replace")
        bar = _bars.get(name)
        if bar is None:
            from tqdm.auto import tqdm

            bar = tqdm(
                total=total or None,
                desc=name,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                leave=False,
                dynamic_ncols=True,
                disable=None,
            )
            _bars[name] = bar
        bar.n = done
        bar.refresh()
        if total and done >= total:
            bar.close()
            del _bars[name]
    except Exception:
        # A broken bar must not break a download.
        pass


_progress_callback = _ffi.callback("void(const char*, uint64_t, uint64_t, void*)", _on_progress)


def _library_path() -> str:
    configured = os.environ.get(LIBRARY_ENV)
    if configured:
        return configured
    name = {"darwin": "libtaco.dylib", "win32": "taco.dll"}.get(sys.platform, "libtaco.so")
    return str(Path(__file__).resolve().parents[1] / "_lib" / name)


def _load() -> Any:
    global _library
    if _library is None:
        with _lock:
            if _library is None:
                path = _library_path()
                try:
                    library = _ffi.dlopen(path)
                except OSError as exc:
                    raise ContainerError(
                        f"could not load the TACO core from {path}: {exc}. "
                        f"Build it with `make core` or set {LIBRARY_ENV}."
                    ) from exc
                if library.taco_api_version() != API_VERSION:
                    raise ContainerError(
                        f"the TACO core at {path} has C API {library.taco_api_version()}, expected {API_VERSION}"
                    )
                library.taco_set_progress(_progress_callback, _ffi.NULL)
                _library = library
    return _library


def _encode(value: str | PathLike[str]) -> bytes:
    return fspath(value).encode("utf-8")


def _check(status: int) -> None:
    if status != 0:
        raise ContainerError(_ffi.string(_load().taco_last_error()).decode("utf-8", errors="replace"))


def _text(pointer: Any) -> str | None:
    return None if pointer == _ffi.NULL else str(_ffi.string(pointer).decode("utf-8"))


def _take(pointer: Any) -> str | None:
    try:
        return _text(pointer)
    finally:
        if pointer != _ffi.NULL:
            _load().taco_free(pointer)


def _call(function: str, *arguments: Any) -> str | None:
    out = _ffi.new("char **")
    _check(getattr(_load(), function)(*arguments, out))
    return _take(out[0])


class NativeDataset:
    """Native handle for an open dataset."""

    __slots__ = ("_handle",)

    def __init__(self, source: str | PathLike[str]) -> None:
        library = _load()
        out = _ffi.new("taco_dataset **")
        _check(library.taco_open(_encode(source), _ffi.NULL, out))
        self._handle = _ffi.gc(out[0], library.taco_close)

    @property
    def source(self) -> str:
        return str(_text(_load().taco_dataset_source(self._handle)))

    @property
    def container(self) -> str:
        return str(_text(_load().taco_dataset_container(self._handle)))

    @property
    def collection(self) -> str:
        return str(_text(_load().taco_dataset_collection(self._handle)))

    @property
    def levels(self) -> list[str]:
        library = _load()
        return [
            str(_text(library.taco_dataset_level(self._handle, index)))
            for index in range(library.taco_dataset_level_count(self._handle))
        ]

    @property
    def structure(self) -> list[str]:
        library = _load()
        return [
            str(_text(library.taco_dataset_structure(self._handle, index)))
            for index in range(library.taco_dataset_structure_count(self._handle))
        ]

    @property
    def derived(self) -> str | None:
        return _text(_load().taco_dataset_derived(self._handle))


def sql(
    datasets: Sequence[NativeDataset],
    *,
    idx: str | None,
    level: str | None,
    pivoted: bool,
    files: Sequence[str] | None,
    location: bool,
) -> str:
    """Build the native read query, combining sources when needed."""
    # cffi owns every buffer below until the call returns.
    keep: list[Any] = []

    def text(value: str | None) -> Any:
        if value is None:
            return _ffi.NULL
        keep.append(_ffi.new("char[]", value.encode("utf-8")))
        return keep[-1]

    options = _ffi.new("taco_read_options *")
    options.idx = text(idx)
    options.level = text(level)
    options.pivoted = int(pivoted)
    options.location = int(location)
    if files is not None:
        array = _ffi.new("char *[]", [text(name) for name in files])
        keep.append(array)
        options.files = array
        options.file_count = len(files)
    handles = _ffi.new("taco_dataset *[]", [dataset._handle for dataset in datasets])
    return str(_call("taco_sql", handles, len(datasets), options))


def profile(source: str | PathLike[str]) -> str:
    return str(_call("taco_profile", _encode(source)))


def fetch(items: Sequence[tuple[str, int, int, str | PathLike[str]]]) -> None:
    """Copy object ranges to local files.

    Items are ``(uri, offset, length, path)``. A zero length reads to EOF.
    """
    if not items:
        return
    keep: list[Any] = []
    array = _ffi.new("taco_fetch_item[]", len(items))
    for slot, (uri, offset, length, path) in zip(array, items, strict=True):
        keep.append(_ffi.new("char[]", _encode(uri)))
        slot.uri = keep[-1]
        slot.offset = offset
        slot.length = length
        keep.append(_ffi.new("char[]", _encode(path)))
        slot.path = keep[-1]
    _check(_load().taco_fetch(array, len(items)))


__all__ = [
    "LIBRARY_ENV",
    "NativeDataset",
    "fetch",
    "profile",
    "sql",
]
