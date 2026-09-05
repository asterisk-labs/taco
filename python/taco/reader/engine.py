from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, cast

from ..errors import ContainerError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

EXTENSION_ENV = "COZIP_EXTENSION"

_lock = threading.Lock()
_state = threading.local()


def connect() -> duckdb.DuckDBPyConnection:
    """Return one cached connection per thread."""
    connection = cast("duckdb.DuckDBPyConnection | None", getattr(_state, "connection", None))
    if connection is not None:
        return connection
    with _lock:
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ContainerError(
                "reading a TACO container needs duckdb and the cozip extension: pip install duckdb"
            ) from exc

        local = os.environ.get(EXTENSION_ENV)
        connection = None
        try:
            config: dict[str, str | bool | int | float | list[str]] = {"TimeZone": "UTC"}
            if local:
                config["allow_unsigned_extensions"] = "true"
                connection = duckdb.connect(config=config)
                connection.load_extension(local)
            else:
                connection = duckdb.connect(config=config)
                connection.install_extension("cozip", repository="community")
                connection.load_extension("cozip")
            available = connection.execute(
                "SELECT count(*) FROM duckdb_functions() WHERE function_name = 'read_taco'"
            ).fetchone()
            if available is None or available[0] == 0:
                raise RuntimeError("the loaded cozip extension does not include the TACO reader")
        except Exception as exc:
            if connection is not None:
                connection.close()
            raise ContainerError(
                f"could not load the cozip DuckDB extension: {exc}. "
                f"Set {EXTENSION_ENV} to a local build while it is unpublished."
            ) from exc
        _state.connection = connection
        return connection


def reset() -> None:
    """Drop the cached connection. Only tests need this."""
    with _lock:
        connection = cast("duckdb.DuckDBPyConnection | None", getattr(_state, "connection", None))
        if connection is not None:
            try:
                connection.close()
            finally:
                del _state.connection
