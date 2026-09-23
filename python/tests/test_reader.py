from __future__ import annotations

import inspect as python_inspect
import shutil
import threading
from pathlib import Path

import pytest

import taco
import taco.reader.dataset as dataset_module
import taco.reader.engine as engine
import taco.reader.inspect as inspect_module
import taco.reader.native as native
from taco.errors import ContainerError

from .datasets import get_case


def test_reader_inspects_an_archive(archive: Path) -> None:
    contract = inspect_module.contract(archive)
    assert contract.column("kind").to_pylist() == ["structure"] * 5 + ["level"] * 4
    assert inspect_module.structure(archive) == [
        "before/B02.tif",
        "before/B03.tif",
        "after/B02.tif",
        "mask.tif",
        "extra*[1,3].png",
    ]
    assert inspect_module.levels(archive) == ["sample", "children", "children/after", "children/before"]
    assert inspect_module.derived(archive) == {}
    assert inspect_module.collection(archive)["id"] == "tiny-change"
    assert inspect_module.profile(archive) == "taco"
    assert "read_parquet(" in inspect_module.native_sql(archive, idx=1)
    with pytest.raises(ValueError, match="layout"):
        inspect_module.native_sql(archive, layout="flat")
    assert taco.inspect(archive, "structure") == inspect_module.structure(archive)
    assert taco.inspect(archive, "contract").equals(contract)
    assert "read_parquet(" in taco.inspect(archive, "native_sql")
    with pytest.raises(ValueError, match="query must be one of"):
        taco.inspect(archive, "unknown")


def test_dataset_api(archive: Path) -> None:
    dataset = taco.open_dataset(archive)

    assert isinstance(dataset, taco.Dataset)
    assert dataset.sources == (archive.resolve(),)
    assert dataset.collection.id == "tiny-change"
    assert dataset.read().num_rows == 4
    assert taco.read(archive).num_rows == 4
    assert repr(dataset).startswith("Dataset(")

    long = dataset.sql(
        "SELECT sample_index, path, \"taco:location\" FROM files WHERE sample_index = 3 AND path = 'mask.tif'"
    )
    assert long.column("path").to_pylist() == ["mask.tif"]
    assert long.column("sample_index").to_pylist() == [3]
    assert long.column("taco:location")[0].as_py().startswith("/vsisubfile/")
    assert "taco:location" not in dataset.sql("SELECT * FROM data").column_names

    level = dataset.sql("SELECT * FROM children__before")
    assert level.num_rows == 8
    assert "internal:current_id" in level.column_names
    assert "taco:location" not in level.column_names
    assert dataset.sql("SELECT * FROM data WHERE sample_index = 1 -- trailing comment").num_rows == 1


def test_high_level_read_signatures() -> None:
    assert list(python_inspect.signature(taco.read).parameters) == ["source", "files"]
    assert list(python_inspect.signature(taco.Dataset.read).parameters) == ["self", "files"]
    assert list(python_inspect.signature(taco.Dataset.sql).parameters) == ["self", "query"]
    assert list(python_inspect.signature(taco.inspect).parameters) == ["path", "query"]


def test_reader_combines_partitions(archive: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy" / "part.zip"
    copy.parent.mkdir()
    shutil.copy(archive, copy)

    dataset = taco.open_dataset([archive, copy])
    assert "sources=2" in repr(dataset)
    assert "2 sources" in dataset._repr_html_()
    wide = dataset.read()
    assert wide.num_rows == 8
    assert list(zip(wide.column("source_file").to_pylist(), wide.column("sample_index").to_pylist(), strict=True)) == [
        ("dataset.zip", 0),
        ("dataset.zip", 1),
        ("dataset.zip", 2),
        ("dataset.zip", 3),
        ("part.zip", 4),
        ("part.zip", 5),
        ("part.zip", 6),
        ("part.zip", 7),
    ]
    assert set(wide.column("source_file").to_pylist()) == {"dataset.zip", "part.zip"}
    assert dataset.sql("SELECT * FROM data WHERE sample_index >= 2 AND sample_index < 4").num_rows == 2
    assert set(dataset.sql("SELECT * FROM sample").column("source_file").to_pylist()) == {
        "dataset.zip",
        "part.zip",
    }

    selected = engine.open_reader().execute(inspect_module.native_sql([archive, copy], idx=4)).to_arrow_table()
    assert selected.column("sample_index").to_pylist() == [4]
    assert selected.column("source_file").to_pylist() == ["part.zip"]
    window = engine.open_reader().execute(inspect_module.native_sql([archive, copy], idx=(3, 5))).to_arrow_table()
    assert window.column("sample_index").to_pylist() == [3, 4]
    assert window.column("source_file").to_pylist() == ["dataset.zip", "part.zip"]


def test_reader_keeps_the_location_of_single_file_samples(tmp_path: Path) -> None:
    case = get_case("single_file")
    path = tmp_path / "single.zip"
    with taco.open_writer(case.collection, path) as writer:
        writer.extend(case.samples)
        writer.run()

    dataset = taco.open_dataset(path)
    assert all(value.startswith("/vsisubfile/") for value in dataset.read().column("data.bin::location").to_pylist())
    assert "taco:location" not in dataset.sql("SELECT * FROM data").column_names


def test_reader_reports_core_errors(archive: Path, tmp_path: Path) -> None:
    with pytest.raises(ContainerError, match=r"unknown structure leaf: nope\.tif"):
        taco.read(archive, files=["nope.tif"])
    with pytest.raises(ContainerError, match="could not open"):
        taco.read(tmp_path / "missing.zip")
    dataset = taco.open_dataset(archive)
    with pytest.raises(TypeError, match="query"):
        dataset.sql(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty"):
        dataset.sql("  ; ")


def test_dataset_rejects_invalid_sources(monkeypatch: pytest.MonkeyPatch, collection: taco.Collection) -> None:
    monkeypatch.setattr(dataset_module, "merge_collections", lambda paths: collection)
    dataset = taco.open_dataset("https://example.com/data.zip")

    assert dataset.sources == ("https://example.com/data.zip",)
    with pytest.raises(ValueError, match="must not be empty"):
        taco.open_dataset("")
    with pytest.raises(ValueError, match="at least one"):
        taco.open_dataset([])
    with pytest.raises(ValueError, match="unique"):
        taco.open_dataset(["same.zip", "same.zip"])


def test_dataset_html_escapes_collection_text(monkeypatch: pytest.MonkeyPatch, collection: taco.Collection) -> None:
    dangerous = collection.replace(title="<dataset>", description="<script>alert(1)</script>")
    monkeypatch.setattr(dataset_module, "merge_collections", lambda paths: dangerous)

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
    assert "extra*[1,3].png" in html
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
    assert dataset.read().num_rows == 4
    assert dataset.sql("SELECT * FROM files").num_rows == 21


def test_file_selection_accepts_one_name(archive: Path) -> None:
    assert taco.read(archive, files="mask.tif").column_names[-1] == "mask.tif::location"
    assert inspect_module.native_sql(archive, files="mask.tif") == inspect_module.native_sql(
        archive, files=["mask.tif"]
    )


def test_sql_relations_and_nested_wide_names(archive: Path) -> None:
    dataset = taco.open_dataset(archive)

    assert "before__B02.tif::location" in dataset.read().column_names
    assert not any(name.startswith("before/") for name in dataset.read().column_names)
    assert dataset.sql("SELECT count(*) AS n FROM data").column("n").to_pylist() == [4]
    files = dataset.sql(
        "SELECT sample_index, path, \"taco:location\" FROM files WHERE path = 'before/B02.tif' ORDER BY sample_index"
    )
    assert files.num_rows == 4
    assert all(value.startswith("/vsisubfile/") for value in files.column("taco:location").to_pylist())
    assert dataset.sql("SELECT count(*) AS n FROM children__before").column("n").to_pylist() == [8]


@pytest.mark.parametrize("value", [1, b"mask.tif", ["mask.tif", 1]])
def test_file_selection_rejects_non_strings(archive: Path, value) -> None:
    with pytest.raises(TypeError, match="files"):
        taco.read(archive, files=value)


def test_engine_keeps_one_connection_per_thread() -> None:
    engine.close_reader()
    connection = engine.open_reader()
    assert engine.open_reader() is connection
    assert connection.execute("SELECT current_setting('TimeZone')").fetchone() == ("UTC",)

    others: list[object] = []
    thread = threading.Thread(target=lambda: others.append(engine.open_reader()))
    thread.start()
    thread.join()
    assert others[0] is not connection

    engine.close_reader()
    assert engine.open_reader() is not connection


def test_native_reports_a_missing_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(native, "_library", None)
    monkeypatch.setenv(native.LIBRARY_ENV, str(tmp_path / "missing-libtaco"))
    with pytest.raises(ContainerError, match="could not load the TACO core"):
        native.profile("dataset.zip")
