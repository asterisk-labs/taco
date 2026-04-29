"""cffi bindings to libcozip. Loads the bundled shared library at import time."""

import os
import sys
from pathlib import Path

import cffi

ffi = cffi.FFI()
ffi.cdef("""
typedef struct { int code; char message[192]; } cozip_error_t;

typedef struct {
    int kind;
    union {
        const char* path;
        struct { const uint8_t* data; size_t size; } buffer;
    } u;
} cozip_source_t;

typedef struct {
    const char* arc_name;
    uint64_t payload_size;
    bool in_index;
    cozip_source_t source;
    uint64_t lfh_offset, lfh_size, payload_offset;
} cozip_entry_t;

int cozip_plan(cozip_entry_t*, size_t, cozip_error_t*);
int cozip_index_payload_size(const cozip_entry_t*, size_t,
                             size_t*, cozip_error_t*);
int cozip_build_index_payload(const cozip_entry_t*, size_t, int,
                              uint8_t*, size_t, cozip_error_t*);
int cozip_write_archive(const char*, const cozip_entry_t*, size_t,
                        const uint8_t*, size_t, cozip_error_t*);
int cozip_patch_integrity_hash(const char*, size_t, cozip_error_t*);
""")


def _lib_filename() -> str:
    """Returns the shared library filename for the current platform.

    No 'lib' prefix anywhere — the CMakeLists sets PREFIX "" so the
    artifact name is identical across Linux/macOS/Windows.
    """
    if sys.platform == "darwin":
        return "cozip.dylib"
    if sys.platform == "win32":
        return "cozip.dll"
    return "cozip.so"  # linux + bsd


def _resolve_lib_path() -> str:
    """Searches for the shared library in several candidate locations."""
    name = _lib_filename()

    # 1. Env var override (for debug / development).
    env = os.environ.get("COZIP_LIB_PATH")
    if env:
        if not Path(env).exists():
            raise ImportError(f"cozip: COZIP_LIB_PATH={env!r} does not exist")
        return env

    # 2. Canonical path: next to this file, under _lib/.
    here = Path(__file__).resolve().parent
    canonical = here / "_lib" / name
    if canonical.exists():
        return str(canonical)

    # 3. Fallback: any neighboring build/ dir (dev mode without reinstall).
    repo = here.parent.parent
    for build_dir in (repo / "python" / "build", repo / "build"):
        if build_dir.is_dir():
            for found in build_dir.rglob(name):
                return str(found)

    raise ImportError(
        f"cozip: native library {name!r} not found.\n"
        f"  Tried: {canonical}\n"
        f"  Fix: run `pip install -e python/` from the repo root, "
        f"or set COZIP_LIB_PATH=/abs/path/to/{name}"
    )


lib = ffi.dlopen(_resolve_lib_path())


class CozipError(RuntimeError):
    """Raised when libcozip returns a non-zero status."""

    def __init__(self, code: int, message: str):
        super().__init__(f"[code {code}] {message}")
        self.code = code
        self.message = message

    @classmethod
    def from_struct(cls, err) -> "CozipError":
        """Builds the exception from a cozip_error_t struct."""
        msg = ffi.string(err.message).decode("utf-8", errors="replace")
        return cls(int(err.code), msg)