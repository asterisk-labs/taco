from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from ..errors import ContainerError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

#: Path to a locally built ``cozip.duckdb_extension``. Set it while the
#: extension is not yet on the DuckDB community registry.
EXTENSION_ENV = "COZIP_EXTENSION"

_lock = threading.Lock()
_connection: duckdb.DuckDBPyConnection | None = None


def connect() -> duckdb.DuckDBPyConnection:
    """Return the process-wide connection, loading the extension once."""
    global _connection
    with _lock:
        if _connection is not None:
            return _connection
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ContainerError(
                "reading a TACO container needs duckdb and the cozip extension: pip install duckdb"
            ) from exc

        local = os.environ.get(EXTENSION_ENV)
        try:
            if local:
                connection = duckdb.connect(config={"allow_unsigned_extensions": True})
                connection.execute(f"LOAD '{local}'")
            else:
                connection = duckdb.connect()
                connection.execute("INSTALL cozip FROM community")
                connection.execute("LOAD cozip")
        except Exception as exc:
            raise ContainerError(
                f"could not load the cozip DuckDB extension: {exc}. "
                f"Set {EXTENSION_ENV} to a local build while it is unpublished."
            ) from exc
        _connection = connection
        return _connection


def reset() -> None:
    """Drop the cached connection. Only tests need this."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None
