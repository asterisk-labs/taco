from __future__ import annotations

import contextlib
import multiprocessing
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

import taco
import taco.reader.engine as engine

needs_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")


def _run_forked(target: Callable[..., None], *args: Any) -> int | None:
    process = multiprocessing.get_context("fork").Process(target=target, args=args)
    process.start()
    process.join(30)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("the forked child hung")
    return process.exitcode


def _read_in_child(dataset: taco.Dataset, parent: duckdb.DuckDBPyConnection) -> None:
    assert dataset.sql("SELECT count(*) AS n FROM dataset").column("n").to_pylist() == [4]
    assert engine.open_reader() is not parent
    # Closes the connection the child opened, never the inherited one.
    engine.close_reader()
    assert dataset.read().num_rows == 4


@needs_fork
def test_forked_child_opens_its_own_connection(archive: Path) -> None:
    dataset = taco.open_dataset(archive)
    assert dataset.read().num_rows == 4
    parent = engine.open_reader()

    assert _run_forked(_read_in_child, dataset, parent) == 0
    assert engine.open_reader() is parent
    assert dataset.read().num_rows == 4


@needs_fork
def test_fork_keeps_the_connections_of_other_threads(archive: Path) -> None:
    dataset = taco.open_dataset(archive)
    parent = engine.open_reader()
    opened = threading.Event()
    done = threading.Event()

    def hold() -> None:
        dataset.read()
        opened.set()
        done.wait()

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        opened.wait()
        # The child frees the locals of this thread while it starts.
        assert _run_forked(_read_in_child, dataset, parent) == 0
    finally:
        done.set()
        thread.join()


def test_reader_keeps_utc_offline(archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No cached ICU and an extension repository that refuses connections, as on an offline machine.
    offline = {"extension_directory": str(tmp_path / "extensions"), "custom_extension_repository": "http://127.0.0.1:9"}
    connect = duckdb.connect

    def offline_connect(*args: Any, config: dict[str, Any] | None = None, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        connection = connect(*args, config={**(config or {}), **offline}, **kwargs)
        # Start outside UTC, as on a host in another timezone.
        with contextlib.suppress(duckdb.Error):
            connection.execute("SET TimeZone = 'America/Lima'")
        return connection

    monkeypatch.setattr(duckdb, "connect", offline_connect)
    monkeypatch.setattr(engine, "_state", threading.local())
    try:
        table = taco.read(archive)
        timezone_setting = engine.open_reader().execute("SELECT current_setting('TimeZone')").fetchone()
    finally:
        engine.close_reader()

    assert timezone_setting in {("UTC",), None}
    assert table.schema.field("stac:datetime").type == pa.timestamp("us", tz="UTC")
    assert table.column("stac:datetime")[0].as_py() == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert not list((tmp_path / "extensions").rglob("*.duckdb_extension"))


class _ConnectionWithoutICU:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        if "TimeZone" in statement:
            raise duckdb.InvalidInputException("the icu extension needs to be loaded")


def test_reader_without_icu_never_installs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _ConnectionWithoutICU()
    monkeypatch.setattr(duckdb, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(engine, "_state", threading.local())
    assert engine.open_reader() is connection
    assert connection.statements == ["SET TimeZone = 'UTC'", "SET autoinstall_known_extensions = false"]


def test_reader_reports_other_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        raise duckdb.IOException("IO Error: no space left on device")

    monkeypatch.setattr(duckdb, "connect", broken)
    monkeypatch.setattr(engine, "_state", threading.local())
    with pytest.raises(duckdb.IOException, match="no space left"):
        engine.open_reader()
