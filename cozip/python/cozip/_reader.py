"""Pure-Python reader for cozip 1.0 archives, local or remote.

One speculative 64 KiB request loads the __cozip__ LFH and the index
payload. After that, a single range request fetches the priority
parquet, which is parsed with pandas and returned with an injected
"cozip:gdal_vsi" column built from the parquet's own offset/size
columns.

No dependency on libcozip. pandas is imported lazily so users that
only need the byte-level reader (future) won't pay for it.
"""

import io
import struct
from pathlib import Path
from urllib.request import Request, urlopen

# Constants from the cozip 1.0 spec (see cozip.h).
_INDEX_OFFSET    = 51
_INDEX_NAME      = b"__cozip__"
_MAGIC           = b"CZIP"
_HEADER_SIZE     = 11   # magic(4) + version(2) + profile(1) + n_entries(4)
_ZIP_SIG         = b"PK\x03\x04"
_BOOTSTRAP_SIZE  = 65536  # 64 KiB speculative read


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _get(source: str, offset: int, size: int, *, is_remote: bool) -> bytes:
    """One range-read primitive. Local seek+read, or HTTP Range."""
    if is_remote:
        req = Request(
            source,
            headers={
                "Range": f"bytes={offset}-{offset + size - 1}",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urlopen(req) as r:  # noqa: S310 (controlled URL)
            status = getattr(r, "status", 200)
            data = r.read()
        # If the server ignored Range and returned 200 with the full
        # file, slice manually. Otherwise trim any tail libcurl-style
        # servers might add.
        if status == 200:
            data = data[offset : offset + size]
        elif len(data) > size:
            data = data[:size]
        if len(data) < size:
            raise IOError(
                f"short read at offset {offset}: got {len(data)}, "
                f"expected {size}"
            )
        return data

    with open(source, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    if len(data) < size:
        raise IOError(
            f"short read at offset {offset}: got {len(data)}, "
            f"expected {size} (truncated file?)"
        )
    return data


def _parse_index(payload: bytes) -> dict[str, tuple[int, int]]:
    """Parse the index payload into {name: (offset, size)}.

    Layout (all little-endian):
      header     : "CZIP" + version u16 + profile u8 + n_entries u32
      name_lens  : n × u16
      names      : concatenated UTF-8, no separators
      offsets    : n × u64
      sizes      : n × u64
    """
    if payload[:4] != _MAGIC:
        raise ValueError(f"bad index magic: {payload[:4]!r}")
    n = struct.unpack_from("<I", payload, 7)[0]

    cur = _HEADER_SIZE
    name_lens = struct.unpack_from(f"<{n}H", payload, cur)
    cur += 2 * n

    names = []
    for nl in name_lens:
        names.append(payload[cur : cur + nl].decode("utf-8"))
        cur += nl

    offsets = struct.unpack_from(f"<{n}Q", payload, cur)
    cur += 8 * n
    sizes = struct.unpack_from(f"<{n}Q", payload, cur)

    return {nm: (off, sz) for nm, off, sz in zip(names, offsets, sizes)}


def _bootstrap(source: str, *, is_remote: bool) -> dict[str, tuple[int, int]]:
    """Pull the head, validate the LFH, parse the index. One request
    in the common case, two if the index doesn't fit in 64 KiB.
    """
    head = _get(source, 0, _BOOTSTRAP_SIZE, is_remote=is_remote)

    if len(head) < _INDEX_OFFSET:
        raise ValueError(f"archive too small: {len(head)} bytes")
    if head[:4] != _ZIP_SIG:
        raise ValueError("not a ZIP archive (bad PK\\x03\\x04 signature)")
    if head[30:39] != _INDEX_NAME:
        raise ValueError("first ZIP entry is not __cozip__")

    # LFH compressed_size = index payload size.
    index_size = struct.unpack_from("<I", head, 18)[0]
    if index_size == 0 or index_size == 0xFFFFFFFF:
        raise ValueError(f"bad index_payload_size in LFH: {index_size}")

    end = _INDEX_OFFSET + index_size
    if end > len(head):
        # Index didn't fit in the bootstrap window. Fetch the rest.
        rest = _get(source, len(head), end - len(head), is_remote=is_remote)
        head += rest

    return _parse_index(head[_INDEX_OFFSET:end])


def _vsi_base(source: str, *, is_remote: bool) -> str:
    """Right-hand side of /vsisubfile/<off>_<size>,<here>."""
    return f"/vsicurl/{source}" if is_remote else str(Path(source).resolve())


def read(source: str):
    """Read the metadata parquet from a FLAT-profile cozip.

    Bootstraps the index from the archive head, locates the
    "__metadata__" entry, fetches its payload, and returns it as a
    DataFrame with one injected column, "cozip:gdal_vsi", carrying a
    /vsisubfile path per row built from the parquet's own 'offset'
    and 'size' columns.

    Args:
        source: Local filesystem path or http(s) URL of the cozip.

    Returns:
        pandas.DataFrame with the original parquet columns plus
        "cozip:gdal_vsi".

    Raises:
        ImportError: pandas is not installed.
        ValueError: the archive is not a valid FLAT cozip, or the
            parquet lacks 'offset'/'size' columns.
        IOError: the read failed (truncated file, server error, etc).
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError(
            "cozip.read requires pandas. Install with `pip install pandas`."
        ) from e

    is_remote = _is_url(source)
    index = _bootstrap(source, is_remote=is_remote)

    if "__metadata__" not in index:
        raise ValueError(
            "archive has no __metadata__ entry "
            "(not a FLAT-profile cozip?). "
            f"Priority entries: {sorted(index)}"
        )

    offset, size = index["__metadata__"]
    payload = _get(source, offset, size, is_remote=is_remote)

    df = pd.read_parquet(io.BytesIO(payload))

    missing = {"offset", "size"} - set(df.columns)
    if missing:
        raise ValueError(
            f"__metadata__ parquet is missing column(s) {sorted(missing)}; "
            f"cannot build cozip:gdal_vsi"
        )

    base = _vsi_base(source, is_remote=is_remote)
    df["cozip:gdal_vsi"] = (
        "/vsisubfile/"
        + df["offset"].astype(str)
        + "_"
        + df["size"].astype(str)
        + ","
        + base
    )

    return df