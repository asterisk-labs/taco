"""High-level archive writer (PROFILE_FLAT only, path sources only).

Walks the libcozip pipeline end-to-end and drops a `__metadata__.parquet`
at the tail with a row per indexed user file. Extra columns from the
input table flow through to the parquet untouched.

For TACO/NONE profiles or buffer sources, drop down to the low-level
API: `cozip.lib.cozip_build_index_payload` etc.
"""

import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ._core import (
    COZIP_PROFILE_FLAT,
    COZIP_SOURCE_PATH,
    CozipError,
    ffi,
    lib,
)

# Names the writer reserves for itself; users cannot use them.
_PADDING_NAME = "__cozip_padding__"
_RESERVED_NAMES = frozenset({"__cozip__", "__metadata__", _PADDING_NAME})

# Constants mirrored from cozip.h and the ZIP APPNOTE. They are used only to
# decide whether the Python writer needs a final non-indexed padding entry
# before the C core patches the 32 KiB integrity suffix.
_COZIP_INDEX_OFFSET = 51
_COZIP_HASH_WINDOW_SIZE = 32768
_COZIP_MIN_ARCHIVE_SIZE = _COZIP_HASH_WINDOW_SIZE + _COZIP_INDEX_OFFSET
_ZIP_CENTRAL_HEADER_BASE_SIZE = 46
_ZIP_EOCD_SIZE = 22

def _check(status: int, err: Any) -> None:
    """Raise a CozipError if a libcozip call returned a non-zero status.

    Args:
        status (int): Return code from a libcozip function call.
        err (Any): Pointer to a populated `cozip_error_t` struct.

    Raises:
        CozipError: When `status != 0`, built from the error struct.
    """
    if status != 0:
        raise CozipError.from_struct(err)


def _build_metadata_parquet(
    table: pa.Table,
    entries: Any,
    n_users: int,
    in_idx: list[bool],
    create_options: dict | None,
    temp_dir: str | Path | None,
) -> Path:
    """Build the `__metadata__.parquet` temp file.

    Filters the input table to in-index rows, drops the columns the
    spec considers writer-private (`path`, `in_index`), and appends
    `offset` (uint64) and `size` (uint64) computed by `cozip_plan`.
    The output column order is `name, offset, size, ...` followed by
    every other user-supplied column in input order.

    Args:
        table (pa.Table): Original input table from the caller.
        entries (Any): cffi `cozip_entry_t[]` array, post-`cozip_plan`,
            so each entry's `payload_offset` and `payload_size` are set.
        n_users (int): Number of user entries (excludes `__metadata__`,
            which sits at index `n_users`).
        in_idx (list[bool]): Per-user-row indexing flag, length `n_users`.
        create_options (dict | None): kwargs forwarded to
            `pyarrow.parquet.write_table`.
        temp_dir (str | Path | None): Directory for the temp parquet.
            None means use `tempfile.gettempdir()` (which respects the
            `TMPDIR` env var).

    Returns:
        Path: Filesystem path of the written `.parquet` file. The caller
            is responsible for deleting it.
    """
    indexed = [i for i in range(n_users) if in_idx[i]]
    offsets = [int(entries[i].payload_offset) for i in indexed]
    sizes   = [int(entries[i].payload_size)   for i in indexed]

    mask = pa.array(in_idx, type=pa.bool_())
    meta = table.filter(mask)
    drop = [c for c in ("path", "in_index") if c in meta.column_names]
    if drop:
        meta = meta.drop_columns(drop)

    meta = meta.append_column("offset", pa.array(offsets, type=pa.uint64()))
    meta = meta.append_column("size",   pa.array(sizes,   type=pa.uint64()))

    # Column order: name, offset, size, ...rest-in-input-order.
    rest = [c for c in meta.column_names if c not in ("name", "offset", "size")]
    meta = meta.select(["name", "offset", "size"] + rest)

    if temp_dir is not None:
        Path(temp_dir).mkdir(parents=True, exist_ok=True)

    tmp = tempfile.NamedTemporaryFile(
        suffix=".parquet",
        dir=str(temp_dir) if temp_dir is not None else None,
        delete=False,
    )
    tmp.close()
    parquet_path = Path(tmp.name)

    try:
        pq.write_table(meta, str(parquet_path), **(create_options or {}))
    except Exception:
        parquet_path.unlink(missing_ok=True)
        raise

    return parquet_path


def _build_padding_file(size: int, temp_dir: str | Path | None) -> Path:
    """Create a non-empty temp payload used only to satisfy cozip min size."""
    if size <= 0:
        raise ValueError("padding size must be positive")
    if temp_dir is not None:
        Path(temp_dir).mkdir(parents=True, exist_ok=True)

    tmp = tempfile.NamedTemporaryFile(
        suffix=".bin",
        dir=str(temp_dir) if temp_dir is not None else None,
        delete=False,
    )
    try:
        tmp.write(b"\x5a" * size)
    finally:
        tmp.close()
    return Path(tmp.name)


def _arc_name_len(entry: Any) -> int:
    """Return the byte length of a cffi entry arc_name."""
    return len(ffi.string(entry.arc_name))


def _predict_zip32_archive_size(
    entries: Any,
    n_entries: int,
    index_payload_size: int,
) -> int:
    """Predict libzip's small-archive ZIP32 size for STORE entries.

    This intentionally covers the small-file path where padding matters.
    ZIP64 archives are already far above the cozip minimum size.
    """
    total = _COZIP_INDEX_OFFSET + int(index_payload_size)

    for i in range(n_entries):
        total += int(entries[i].lfh_size) + int(entries[i].payload_size)

    total += _ZIP_CENTRAL_HEADER_BASE_SIZE + len("__cozip__")
    for i in range(n_entries):
        total += _ZIP_CENTRAL_HEADER_BASE_SIZE + _arc_name_len(entries[i])

    total += _ZIP_EOCD_SIZE
    return total


def _required_padding_size(
    entries: Any,
    n_entries: int,
    index_payload_size: int,
) -> int:
    """Return payload bytes needed for the optional padding entry."""
    predicted = _predict_zip32_archive_size(entries, n_entries, index_payload_size)
    missing = _COZIP_MIN_ARCHIVE_SIZE - predicted
    if missing <= 0:
        return 0

    name_len = len(_PADDING_NAME)
    entry_overhead = (
        30 + name_len
        + _ZIP_CENTRAL_HEADER_BASE_SIZE + name_len
    )
    return max(1, missing - entry_overhead)

def create(
    out_path: str | Path,
    table: pa.Table,
    create_options: dict | None = None,
    temp_dir: str | Path | None = None,
) -> str:
    """Build a FLAT-profile .cozip archive on disk.

    Walks the libcozip pipeline end-to-end: plan offsets, drop a
    `__metadata__.parquet` describing the indexed user files, serialize
    the cozip index, write the archive via libzip, and patch the
    FNV-1a 64 integrity hash. The output file is overwritten if it
    exists. The temp parquet is always cleaned up.

    The metadata parquet contains: `name` (str), `offset` (uint64),
    `size` (uint64), plus every additional column from `table` other
    than `path` and `in_index`. Only rows with `in_index=True` are
    listed.

    Args:
        out_path (str | Path): Destination file path. Resolved to an
            absolute path before writing.
        table (pa.Table): Source entries. Must provide 'name' (str) and
            'path' (str). Optional 'in_index' (bool, default True)
            controls whether the entry is listed in the cozip index
            and the metadata parquet. Any other columns flow through
            to the metadata parquet.
        create_options (dict | None, optional): Forwarded as kwargs to
            `pyarrow.parquet.write_table` when writing the metadata
            parquet. Use this to set compression, row_group_size, etc.
            Defaults to None (pyarrow defaults).
        temp_dir (str | Path | None, optional): Directory where the
            temporary metadata parquet is written before being absorbed
            into the archive. Created if missing. Defaults to None,
            which uses `tempfile.gettempdir()` (respects the `TMPDIR`
            env var, useful when /tmp is restricted or full).

    Raises:
        ValueError: A required column is missing, the table is empty,
            a row uses a reserved name, or names contain duplicates.
        FileNotFoundError: A row's source path does not exist.
        CozipError: A libcozip call failed (I/O, allocation, malformed
            archive output, etc.).

    Returns:
        str: Absolute path of the created archive.
    """
    cols = table.column_names
    missing = {"name", "path"} - set(cols)
    if missing:
        raise ValueError(
            f"cozip.create: table is missing required column(s): "
            f"{sorted(missing)}"
        )

    n_users = len(table)
    if n_users == 0:
        raise ValueError("cozip.create: empty entry list")

    names = table.column("name").to_pylist()
    paths = table.column("path").to_pylist()
    in_idx = (
        table.column("in_index").to_pylist()
        if "in_index" in cols
        else [True] * n_users
    )

    # Reserved names + duplicates: format-level rules belong to the
    # binding, not to libcozip (cozip.c only protects memory).
    seen: set[str] = set()
    for i, name in enumerate(names):
        if name in _RESERVED_NAMES:
            raise ValueError(
                f"cozip.create: row {i} uses reserved name {name!r}"
            )
        if name in seen:
            raise ValueError(
                f"cozip.create: duplicate name {name!r} at row {i}"
            )
        seen.add(name)

    out_path = str(Path(out_path).resolve())
   
    # Allocate one extra slot for __metadata__ and one optional
    # non-indexed padding entry. __metadata__ sits after user files so
    # its size never shifts user offsets; padding sits after metadata.
    n_total = n_users + 1
    pad_idx = n_users + 1
    entries = ffi.new(f"cozip_entry_t[{n_users + 2}]")
    keepalive: list[Any] = []

    for i in range(n_users):
        path = Path(paths[i])
        if not path.exists():
            raise FileNotFoundError(
                f"cozip.create: row {i} ({names[i]!r}): "
                f"source file not found: {path}"
            )

        name_c = ffi.new("char[]", names[i].encode("utf-8"))
        path_c = ffi.new("char[]", str(path).encode("utf-8"))
        keepalive.extend((name_c, path_c))

        entries[i].arc_name      = name_c
        entries[i].payload_size  = path.stat().st_size
        entries[i].in_index      = bool(in_idx[i])
        entries[i].source.kind   = COZIP_SOURCE_PATH
        entries[i].source.u.path = path_c

    # __metadata__ entry: real size and source path are unknown until
    # we generate the parquet. Use 0/NULL placeholders for the first
    # cozip_plan call.
    meta_idx = n_users
    meta_name_c = ffi.new("char[]", b"__metadata__")
    keepalive.append(meta_name_c)

    entries[meta_idx].arc_name      = meta_name_c
    entries[meta_idx].payload_size  = 0
    entries[meta_idx].in_index      = True
    entries[meta_idx].source.kind   = COZIP_SOURCE_PATH
    entries[meta_idx].source.u.path = ffi.NULL

    err = ffi.new("cozip_error_t*")

    # 1. First plan: produces correct payload_offset/size for every
    #    user entry. The __metadata__ slot's offset is also correct
    #    (it sits last and its placeholder size doesn't perturb anyone
    #    earlier in the archive).
    _check(lib.cozip_plan(entries, n_total, err), err)

    # 2. Build the metadata parquet using the user offsets we just got.
    parquet_path = _build_metadata_parquet(
        table, entries, n_users, in_idx, create_options, temp_dir
    )

    padding_path: Path | None = None

    try:
        # 3. Now we know the real parquet size — patch it into the
        #    __metadata__ entry and re-plan. Re-planning is cheap and
        #    handles the rare case where the parquet crosses 4 GiB and
        #    needs a ZIP64 LFH extra (which would shift the metadata
        #    payload_offset by 20 bytes; user offsets stay untouched).
        parquet_size = parquet_path.stat().st_size
        meta_path_c = ffi.new("char[]", str(parquet_path).encode("utf-8"))
        keepalive.append(meta_path_c)

        entries[meta_idx].payload_size  = parquet_size
        entries[meta_idx].source.u.path = meta_path_c

        _check(lib.cozip_plan(entries, n_total, err), err)

        # 4. Size the cozip index payload buffer.
        idx_size = ffi.new("size_t*")
        _check(
            lib.cozip_index_payload_size(entries, n_total, idx_size, err),
            err,
        )

        # Small archives need a non-indexed padding entry so the
        # integrity suffix can cover the final 32 KiB without colliding
        # with the mutable hash slot in the first LFH.
        pad_size = _required_padding_size(entries, n_total, int(idx_size[0]))
        if pad_size:
            padding_path = _build_padding_file(pad_size, temp_dir)
            pad_name_c = ffi.new("char[]", _PADDING_NAME.encode("utf-8"))
            pad_path_c = ffi.new("char[]", str(padding_path).encode("utf-8"))
            keepalive.extend((pad_name_c, pad_path_c))

            entries[pad_idx].arc_name      = pad_name_c
            entries[pad_idx].payload_size  = pad_size
            entries[pad_idx].in_index      = False
            entries[pad_idx].source.kind   = COZIP_SOURCE_PATH
            entries[pad_idx].source.u.path = pad_path_c

            n_total += 1
            _check(lib.cozip_plan(entries, n_total, err), err)
            _check(
                lib.cozip_index_payload_size(entries, n_total, idx_size, err),
                err,
            )

        # 5. Serialize the cozip index payload (profile = FLAT).
        payload = ffi.new(f"uint8_t[{idx_size[0]}]")
        _check(
            lib.cozip_build_index_payload(
                entries, n_total, COZIP_PROFILE_FLAT, payload, idx_size[0], err
            ),
            err,
        )
        
        # 6. Write the archive (libzip-backed).
        out_path_b = out_path.encode("utf-8")
        _check(
            lib.cozip_write_archive(
                out_path_b, entries, n_total, payload, idx_size[0], err
            ),
            err,
        )

        # 7. Patch the FNV-1a 64 integrity hash into bytes 43..50.
        _check(
            lib.cozip_patch_integrity_hash(out_path_b, idx_size[0], err),
            err,
        )
    finally:
        parquet_path.unlink(missing_ok=True)
        if padding_path is not None:
            padding_path.unlink(missing_ok=True)

    return out_path
