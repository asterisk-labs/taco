from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest

import taco
import taco.reader as reader
import taco.reader.engine as engine
from taco.errors import ContainerError


class Result:
    def __init__(self, value=None, table: pa.Table | None = None) -> None:
        self.value = value
        self.table = table or pa.table({"value": [1]})

    def fetchone(self):
        return None if self.value is ... else (self.value,)

    def to_arrow_table(self) -> pa.Table:
        return self.table


class Connection:
    def __init__(self) -> None:
        self.calls = []
        self.installed = []
        self.loaded = []
        self.closed = False

    def execute(self, query, arguments=None) -> Result:
        self.calls.append((query, arguments))
        if "duckdb_functions" in query:
            return Result(1)
        if "taco_structure" in query:
            return Result(["a.bin"])
        if "taco_levels" in query:
            return Result(["sample", "children"])
        if "taco_derived" in query:
            return Result(['{"sample":{"grid":{}}}'])
        if "taco_collection" in query:
            return Result(json.dumps({"id": "x"}))
        if "cozip_profile" in query:
            return Result("taco")
        if "taco_sql" in query:
            return Result("SELECT 1")
        return Result()

    def install_extension(self, name, repository=None) -> None:
        self.installed.append((name, repository))

    def load_extension(self, name) -> None:
        self.loaded.append(name)

    def close(self) -> None:
        self.closed = True


def test_reader_wrappers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert taco.reader is reader
    connection = Connection()
    monkeypatch.setattr(reader, "connect", lambda: connection)
    path = tmp_path / "data.zip"
    assert reader.read(path, idx=(1, 4), files=["a.bin"]).num_rows == 1
    assert reader.contract(path).num_rows == 1
    assert reader.structure(path) == ["a.bin"]
    assert reader.levels(path) == ["sample", "children"]
    assert reader.derived(path) == {"sample": {"grid": {}}}
    assert reader.collection(path) == {"id": "x"}
    assert reader.profile(path) == "taco"
    assert reader.sql(path, idx=1) == "SELECT 1"
    assert "[1, 4]" in str(connection.calls[0][1])


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (3, "3"), ((1, 4), "[1, 4]")],
)
def test_index_encoding(value, expected) -> None:
    assert reader._idx(value) == expected


@pytest.mark.parametrize("value", [True, [1], [1, 2, 3], [0, True]])
def test_invalid_indexes(value) -> None:
    with pytest.raises(TypeError, match="idx"):
        reader._idx(value)


def test_reader_rejects_bad_extension_results(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    monkeypatch.setattr(reader, "connect", lambda: connection)
    monkeypatch.setattr(connection, "execute", lambda *args: Result(3))
    with pytest.raises(ContainerError, match="string list"):
        reader.structure("x")
    with pytest.raises(ContainerError, match="COLLECTION"):
        reader.collection("x")
    with pytest.raises(ContainerError, match="invalid string"):
        reader.profile("x")
    with pytest.raises(ContainerError, match="invalid SQL"):
        reader.sql("x")

    monkeypatch.setattr(connection, "execute", lambda *args: Result("[]"))
    with pytest.raises(ContainerError, match="non-object"):
        reader.collection("x")

    for value in (["{}", "{}"], ["[1]"], ["{"]):
        monkeypatch.setattr(connection, "execute", lambda *args, value=value: Result(value))
        with pytest.raises(ContainerError, match="taco:derived"):
            reader.derived("x")

    monkeypatch.setattr(connection, "execute", lambda *args: Result(...))
    with pytest.raises(ContainerError, match="no row"):
        reader.structure("x")


def test_engine_loads_and_caches_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    fake_duckdb = SimpleNamespace(connect=lambda config: connection)
    monkeypatch.setitem(sys.modules, "duckdb", fake_duckdb)
    monkeypatch.setattr(engine, "_state", threading.local())
    monkeypatch.delenv(engine.EXTENSION_ENV, raising=False)
    assert engine.connect() is connection
    assert engine.connect() is connection
    assert connection.installed == [("cozip", "community")]
    assert connection.loaded == ["cozip"]
    engine.reset()
    assert connection.closed


def test_engine_loads_local_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    monkeypatch.setitem(sys.modules, "duckdb", SimpleNamespace(connect=lambda config: connection))
    monkeypatch.setattr(engine, "_state", threading.local())
    monkeypatch.setenv(engine.EXTENSION_ENV, "/tmp/cozip.duckdb_extension")
    assert engine.connect() is connection
    assert connection.installed == []
    assert connection.loaded == ["/tmp/cozip.duckdb_extension"]
    engine.reset()


def test_engine_reports_missing_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    native_execute = connection.execute

    def execute(query, arguments=None):
        if "duckdb_functions" in query:
            return Result(0)
        return native_execute(query, arguments)

    connection.execute = execute
    monkeypatch.setitem(sys.modules, "duckdb", SimpleNamespace(connect=lambda config: connection))
    monkeypatch.setattr(engine, "_state", threading.local())
    monkeypatch.delenv(engine.EXTENSION_ENV, raising=False)
    with pytest.raises(ContainerError, match="does not include"):
        engine.connect()
    assert connection.closed
