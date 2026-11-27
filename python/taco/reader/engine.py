from __future__ import annotations

import os
import threading
import weakref
from typing import TYPE_CHECKING, cast

from ..errors import ContainerError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import duckdb

_state = threading.local()
_lock = threading.Lock()
# Do not retain connections owned by threads that have exited.
_connections: weakref.WeakSet[duckdb.DuckDBPyConnection] = weakref.WeakSet()
_forking: list[duckdb.DuckDBPyConnection] = []
# Keep inherited connections alive but unused in forked children; closing them
# can hang DuckDB.
_inherited: list[object] = []


def _connect() -> duckdb.DuckDBPyConnection:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ContainerError("reading a TACO dataset needs duckdb: pip install duckdb") from exc

    connection = duckdb.connect()
    # Set UTC after connecting; a config value can trigger an ICU download.
    try:
        connection.execute("SET TimeZone = 'UTC'")
    except duckdb.Error:
        # Without ICU, DuckDB already uses UTC. Prevent later autoinstallation.
        connection.execute("SET autoinstall_known_extensions = false")
    return connection


def open_reader() -> duckdb.DuckDBPyConnection:
    """Return one cached DuckDB connection per thread."""
    connection = cast("duckdb.DuckDBPyConnection | None", getattr(_state, "connection", None))
    if connection is not None:
        return connection
    # Hold the lock while opening so a fork captures every live connection.
    with _lock:
        connection = _connect()
        _connections.add(connection)
    _state.connection = connection
    return connection


def close_reader() -> None:
    """Close the cached connection of the current thread."""
    connection = cast("duckdb.DuckDBPyConnection | None", getattr(_state, "connection", None))
    if connection is not None:
        try:
            connection.close()
        finally:
            del _state.connection


def _before_fork() -> None:
    # Retain other threads' connections before the child loses their locals.
    _lock.acquire()
    _forking.extend(_connections)


def _after_fork_in_parent() -> None:
    _forking.clear()
    _lock.release()


def _after_fork_in_child() -> None:
    global _connections, _lock, _state
    _inherited.extend((_state, *_forking))
    _forking.clear()
    _connections = weakref.WeakSet()
    _lock = threading.Lock()
    _state = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=_before_fork, after_in_parent=_after_fork_in_parent, after_in_child=_after_fork_in_child)


__all__ = ["close_reader", "open_reader"]
