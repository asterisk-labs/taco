from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_PARQUET_OPTIONS: dict[str, Any] = {
    "compression": "zstd",
    "write_statistics": True,
}


def parquet_writer_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    if options and "row_group_size" in options:
        raise ValueError("pass row_group_size as a writer argument, not in parquet_options")
    return DEFAULT_PARQUET_OPTIONS | dict(options or {})
