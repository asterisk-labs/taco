"""Quick test: build a .cozip with the C library and check it's a valid ZIP."""

import sys
import zipfile
from pathlib import Path
import cffi

HERE = Path(__file__).resolve().parent
LIB = HERE / "build" / ("libcozip.dylib" if sys.platform == "darwin"
                        else "libcozip.so" if sys.platform.startswith("linux")
                        else "cozip.dll")

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
int cozip_index_payload_size(const cozip_entry_t*, size_t, size_t*, cozip_error_t*);
int cozip_build_index_payload(const cozip_entry_t*, size_t, int,
                              uint8_t*, size_t, cozip_error_t*);
int cozip_write_archive(const char*, const cozip_entry_t*, size_t,
                        const uint8_t*, size_t, cozip_error_t*);
int cozip_patch_integrity_hash(const char*, size_t, cozip_error_t*);
""")
lib = ffi.dlopen(str(LIB))


def die(label, err):
    sys.exit(f"FAIL {label}: {ffi.string(err.message).decode()}")


# 3 entries: 2 user files + 1 priority "metadata" (in_index for FLAT profile).
# Spec requires archive >= 32768 + 51 bytes, so we use realistic sizes.
specs = [
    ("data/a.bin",   b"A" * 16384),
    ("data/b.bin",   b"B" * 16384),
    ("__metadata__", b"M" * 1024),
]

arr = ffi.new(f"cozip_entry_t[{len(specs)}]")
holders = []
for i, (name, data) in enumerate(specs):
    nh = ffi.new("char[]", name.encode())
    dh = ffi.new("uint8_t[]", data)
    holders += [nh, dh]
    arr[i].arc_name      = nh
    arr[i].payload_size  = len(data)
    arr[i].in_index      = (name == "__metadata__")
    arr[i].source.kind   = 2   # COZIP_SOURCE_BUFFER
    arr[i].source.u.buffer.data = dh
    arr[i].source.u.buffer.size = len(data)

err = ffi.new("cozip_error_t*")

if lib.cozip_plan(arr, len(specs), err) != 0:
    die("plan", err)

sz = ffi.new("size_t*")
if lib.cozip_index_payload_size(arr, len(specs), sz, err) != 0:
    die("index_payload_size", err)

idx_buf = ffi.new(f"uint8_t[{sz[0]}]")
if lib.cozip_build_index_payload(arr, len(specs), 1, idx_buf, sz[0], err) != 0:
    die("build_index_payload", err)

out = HERE / "out.cozip"
out.unlink(missing_ok=True)

if lib.cozip_write_archive(str(out).encode(), arr, len(specs),
                           idx_buf, sz[0], err) != 0:
    die("write_archive", err)

if lib.cozip_patch_integrity_hash(str(out).encode(), sz[0], err) != 0:
    die("patch_integrity_hash", err)

# Sanity: is it a valid ZIP, and does it have what we asked for?
with zipfile.ZipFile(out, "r") as z:
    names = z.namelist()

print(f"wrote {out.name}  ({out.stat().st_size} bytes)")
print(f"entries: {names}")

assert names[0] == "__cozip__",        "first entry must be __cozip__"
assert "__metadata__"  in names,       "__metadata__ missing"
assert "data/a.bin"    in names,       "data/a.bin missing"
assert "data/b.bin"    in names,       "data/b.bin missing"
print("OK")