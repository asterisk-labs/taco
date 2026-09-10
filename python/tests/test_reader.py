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


def test_dataset_api(monkeypatch: pytest.MonkeyPatch, collection: taco.Collection, tmp_path: Path) -> None:
    connection = Connection()
    monkeypatch.setattr(reader, "connect", lambda: connection)
    monkeypatch.setattr(reader, "_collections", lambda paths: [collection.to_dict() for _ in paths])
    path = tmp_path / "data.zip"

    dataset = taco.open_dataset(path)

    assert isinstance(dataset, taco.Dataset)
    assert dataset.sources == (path.resolve(),)
    assert dataset.collection.to_dict() == collection.to_dict()
    assert dataset.contract == collection.contract
    assert taco.read(dataset).num_rows == 1
    assert taco.read(path).num_rows == 1
    assert taco.read(dataset, layout="long", idx=3, level="children", files=["mask.tif"], location=False).num_rows == 1
    assert "pivoted := ?" in connection.calls[-1][0]
    assert connection.calls[-1][1] == [str(path.resolve()), "3", "children", False, ["mask.tif"], False]
    assert repr(dataset).startswith("Dataset(")


def test_reader_uses_location_and_normalizes_legacy_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    class LegacyConnection(Connection):
        def execute(self, query, arguments=None) -> Result:
            if "location := ?" in query:
                raise RuntimeError("read_taco location does not support the supplied arguments; candidate has gdal_vsi")
            self.calls.append((query, arguments))
            return Result(
                table=pa.table(
                    {
                        "path": ["scene.rumi"],
                        "cozip:location": ["stored-flat"],
                        "taco:location": ["stored-taco"],
                        "cozip:gdal_vsi": ["/vsisubfile/1_2,/vsicurl/data.zip"],
                    }
                )
            )

    connection = LegacyConnection()
    monkeypatch.setattr(reader, "connect", lambda: connection)

    table = reader.read("data.zip", pivoted=False)

    assert table.column_names == ["path", "taco:location"]
    assert table["taco:location"].to_pylist() == ["/vsisubfile/1_2,/vsicurl/data.zip"]
    assert "gdal_vsi := ?" in connection.calls[-1][0]


def test_reader_removes_locations_from_raw_and_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection()
    connection.execute = lambda query, arguments=None: Result(  # type: ignore[method-assign]
        table=pa.table(
            {
                "value": [1],
                "cozip:location": ["stored-flat"],
                "taco:location": ["generated-or-stored"],
                "cozip:gdal_vsi": ["legacy"],
            }
        )
    )
    monkeypatch.setattr(reader, "connect", lambda: connection)

    assert reader.read("data.zip", level="children").column_names == ["value"]
    assert reader.read("data.zip", pivoted=False, location=False).column_names == ["value"]


def test_dataset_multiple_sources(monkeypatch: pytest.MonkeyPatch, collection: taco.Collection, tmp_path: Path) -> None:
    connection = Connection()
    monkeypatch.setattr(reader, "connect", lambda: connection)
    monkeypatch.setattr(reader, "_collections", lambda paths: [collection.to_dict() for _ in paths])
    paths = [tmp_path / "a.zip", tmp_path / "b.zip"]

    dataset = taco.open_dataset(paths)
    assert dataset.sources == tuple(path.resolve() for path in paths)
    assert "sources=2" in repr(dataset)
    assert "2 sources" in dataset._repr_html_()

    taco.read(dataset, layout="long", idx=(2, 4))

    query, arguments = connection.calls[-1]
    assert query.count("read_taco") == 2
    assert "UNION ALL BY NAME" in query
    assert arguments == [
        "a.zip",
        str(paths[0].resolve()),
        "[2, 4]",
        None,
        False,
        None,
        True,
        "b.zip",
        str(paths[1].resolve()),
        "[2, 4]",
        None,
        False,
        None,
        True,
    ]
    assert taco.read(paths).num_rows == 1


def test_dataset_rejects_unknown_layout(monkeypatch: pytest.MonkeyPatch, collection: taco.Collection) -> None:
    monkeypatch.setattr(reader, "_collections", lambda paths: [collection.to_dict() for _ in paths])
    dataset = taco.open_dataset("https://example.com/data.zip")

    assert dataset.sources == ("https://example.com/data.zip",)
    with pytest.raises(ValueError, match="must not be empty"):
        taco.open_dataset("")
    with pytest.raises(ValueError, match="at least one"):
        taco.open_dataset([])
    with pytest.raises(ValueError, match="unique"):
        taco.open_dataset(["same.zip", "same.zip"])
    with pytest.raises(ValueError, match="layout"):
        taco.read(dataset, layout="flat")  # type: ignore[arg-type]


def test_dataset_html_escapes_collection_text(monkeypatch: pytest.MonkeyPatch, collection: taco.Collection) -> None:
    dangerous = collection.replace(title="<dataset>", description="<script>alert(1)</script>")
    monkeypatch.setattr(reader, "_collections", lambda paths: [dangerous.to_dict() for _ in paths])

    html = taco.open_dataset("dataset.zip")._repr_html_()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;dataset&gt;" in html
    assert "taco.Dataset" in html
    assert "<svg" in html
    assert "Structure" in html
    assert "Metadata" in html
    assert 'class="taco-structure-graph"' in html
    assert '<g class="taco-graph-node taco-graph-folder">' in html
    assert '<g class="taco-graph-node taco-graph-variable">' in html
    assert ">before/<" in html
    assert "extra*[0,3].png" in html
    assert 'role="tooltip"' in html
    assert "Dataset split" in html
    assert "<span>nullable</span><code>false</code>" in html
    assert 'tabindex="0"' not in html
    assert ".taco-field:hover>.taco-field-info" in html


def test_dataset_reads_folder(folder_dataset: Path) -> None:
    dataset = taco.open_dataset(folder_dataset)

    assert dataset.collection.id == "tiny-change"
    html = dataset._repr_html_()
    assert ">FOLDER<" in html
    assert 'aria-label="TACO folder storage"' in html
    assert taco.read(dataset).num_rows == 4
    assert taco.read(dataset, layout="long").num_rows == 19


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
