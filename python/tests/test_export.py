from __future__ import annotations

import os
import re
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest

import taco
import taco.writer.export as export_module
from taco.container.view import DatasetView, open_view
from taco.contract.naming import RELATIVE_PATH, SOURCE_FILE
from taco.contract.types import type_name
from taco.errors import ContainerError, WriterError
from taco.writer.export import _metadata_path

from .datasets import CASES, DatasetCase, case_id
from .test_writer_cases import data_files, write_case


def assert_same_dataset(expected: DatasetView, actual: DatasetView) -> None:
    assert actual.collection_json == expected.collection_json
    assert actual.levels == expected.levels
    for level in expected.levels:
        table = expected.level(level)
        exported = actual.level(level)
        assert exported.schema.metadata == table.schema.metadata
        exported = exported.select(table.column_names)
        assert [describe(field) for field in exported.schema] == [describe(field) for field in table.schema]
        assert exported.to_pylist() == table.to_pylist()


def describe(field: pa.Field) -> tuple[object, ...]:
    # COLLECTION.json names a struct type without the nullability of its
    # children, so a dataset written from it matches the contract by name.
    return field.name, type_name(field.type), field.nullable, field.metadata


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_export_copies_every_case(case: DatasetCase, tmp_path: Path) -> None:
    direct_path = tmp_path / "direct"
    archive_path = tmp_path / "source.zip"
    write_case(case, direct_path)
    write_case(case, archive_path)

    exported_path = tmp_path / "exported"
    result = taco.export(archive_path, exported_path)

    assert result.samples == len(case.samples)
    assert taco.validate(exported_path).ok
    assert_same_dataset(open_view(direct_path), open_view(exported_path))
    assert data_files(exported_path) == data_files(direct_path)

    repacked_path = tmp_path / "repacked.zip"
    taco.export(exported_path, repacked_path)
    assert taco.validate(repacked_path).ok
    assert_same_dataset(open_view(direct_path), open_view(repacked_path))


def test_export_enables_writer_progress(archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    open_writer = export_module.open_writer

    def capture(*args, **kwargs):
        calls.append(kwargs["progress"])
        return open_writer(*args, **kwargs)

    monkeypatch.setattr(export_module, "open_writer", capture)
    taco.export(archive, tmp_path / "copy.zip")

    assert calls == [True]


def test_export_does_not_index_every_sample_in_a_zip(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(_self):
        raise AssertionError("ZIP export must not build a sample origin map")

    monkeypatch.setattr(export_module._Source, "sample_origins", unexpected)
    result = taco.export(archive, tmp_path / "copy.zip")
    assert result.samples == 4


def test_export_overwrites_an_existing_output(archive: Path, tmp_path: Path) -> None:
    output = tmp_path / "copy.zip"
    taco.export(archive, output)

    with pytest.raises(FileExistsError, match="overwrite=True"):
        taco.export(archive, output)

    result = taco.export(archive, output, overwrite=True)

    assert result.path == output
    assert taco.validate(output).ok


def test_export_subset_matches_direct_write(
    archive: Path, tmp_path: Path, collection: taco.Collection, make_sample
) -> None:
    subset = collection.replace(id="tiny-change-val", description="Validation samples")
    direct = tmp_path / "direct"
    with taco.open_writer(subset, direct) as writer:
        writer.extend([make_sample(1, 1), make_sample(3, 1)])
        writer.run()

    output = tmp_path / "val"
    result = taco.export(
        archive,
        output,
        samples=taco.read(archive).filter(pc.field("ml:split") == "val"),
        id="tiny-change-val",
        description="Validation samples",
    )

    assert result.samples == 2
    assert taco.validate(output).ok
    assert_same_dataset(open_view(direct), open_view(output))
    assert data_files(output) == data_files(direct)


def test_export_subset_to_zip(archive: Path, tmp_path: Path) -> None:
    output = tmp_path / "middle.zip"
    taco.export(
        taco.open_dataset(archive),
        output,
        samples=taco.open_dataset(archive)
        .sql("SELECT * FROM data WHERE sample_index >= 1 AND sample_index < 3")
        .to_pandas(),
        id="tiny-change-middle",
        description="Second and third samples",
    )

    dataset = open_view(output)
    assert taco.validate(output).ok
    assert dataset.level("sample").column("ml:cloud_cover").to_pylist() == [10.5, 21.0]
    with zipfile.ZipFile(output) as archive_file:
        assert archive_file.read("DATA/0/mask.tif") == b"1:mask.tif"
        assert archive_file.read("DATA/1/extra1.png") == b"2:extra1.png"


def test_export_tacocat(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "parts" / "dataset.zip", partition_size=1) as writer:
        writer.extend(make_sample(index) for index in range(4))
        catalog = writer.run().path

    merged = tmp_path / "merged"
    taco.export(catalog, merged)
    assert taco.validate(merged).ok
    assert open_view(merged).sample_count == 4
    assert "taco:sources" not in open_view(merged).collection_json

    train = tmp_path / "train.zip"
    train_rows = taco.read(catalog).filter(pc.field("ml:split") == "train")
    train_rows = train_rows.drop_columns(["source_file"])
    taco.export(catalog, train, samples=train_rows, id="tiny-change-train", description="Training samples")
    dataset = open_view(train)
    assert taco.validate(train).ok
    assert dataset.level("sample").column("ml:cloud_cover").to_pylist() == [0.0, 21.0]
    with zipfile.ZipFile(train) as archive_file:
        assert archive_file.read("DATA/1/mask.tif") == b"2:mask.tif"


def test_collection_fields_are_inherited_unless_given(archive: Path, tmp_path: Path) -> None:
    output = tmp_path / "subset.zip"
    taco.export(
        archive,
        output,
        samples=taco.open_dataset(archive).sql("SELECT * FROM data WHERE sample_index = 0"),
        id="tiny-change-0",
    )
    exported = open_view(output).collection
    source = open_view(archive).collection
    assert exported.id == "tiny-change-0"
    assert exported.description == source.description
    assert exported.licenses == source.licenses
    assert exported.contract == source.contract

    with pytest.raises(TypeError, match="unexpected keyword"):
        taco.export(archive, tmp_path / "bad.zip", nonsense="x")
    with pytest.raises(ValueError, match="keeps the contract"):
        taco.export(archive, tmp_path / "bad.zip", contract=source.contract)
    with pytest.raises(WriterError, match="without samples"):
        taco.export(archive, tmp_path / "none.zip", samples=taco.read(archive).slice(0, 0), id="tiny-change-none")
    assert not (tmp_path / "none.zip").exists()


def test_samples_must_name_rows_of_the_source(
    archive: Path, tmp_path: Path, collection: taco.Collection, make_sample
) -> None:
    with pytest.raises(ValueError, match="sample_index"):
        taco.export(archive, tmp_path / "bad.zip", samples=pa.table({"x": [1]}), id="tiny-change-x")
    with pytest.raises(ValueError, match=r"not found.*999"):
        taco.export(
            archive,
            tmp_path / "bad.zip",
            samples=pa.table({"sample_index": [0, 999]}),
            id="tiny-change-x",
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        taco.export(
            archive,
            tmp_path / "bad.zip",
            samples=pa.table({"sample_index": [-1]}),
            id="tiny-change-x",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (RELATIVE_PATH, "0/../../escaped.bin"),
        (RELATIVE_PATH, "/tmp/escaped.bin"),
        (SOURCE_FILE, "../other.zip"),
        (SOURCE_FILE, "/tmp/other.zip"),
    ],
)
def test_export_rejects_unsafe_metadata_paths(field: str, value: str) -> None:
    with pytest.raises(ContainerError, match="invalid dataset metadata"):
        _metadata_path({field: value}, field)


def test_export_reads_one_dataset(archive: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one dataset"):
        taco.export([archive, tmp_path / "other.zip"], tmp_path / "out.zip")


class RangeHandler(SimpleHTTPRequestHandler):
    """Serve HTTP range requests."""

    def do_GET(self) -> None:
        header = self.headers.get("Range")
        target = Path(self.translate_path(self.path))
        if header is None or not target.is_file():
            super().do_GET()
            return
        data = target.read_bytes()
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
        assert match is not None
        first = int(match[1])
        last = int(match[2]) if match[2] else len(data) - 1
        chunk = data[first : last + 1]
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {first}-{first + len(chunk) - 1}/{len(data)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def server(root: Path) -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args, **kwargs: RangeHandler(*args, directory=root, **kwargs))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def test_export_from_remote_archive_and_folder(archive: Path, folder_dataset: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    with server(tmp_path) as base:
        subset = taco.export(
            f"{base}/dataset.zip",
            output / "middle.zip",
            samples=taco.open_dataset(f"{base}/dataset.zip").sql(
                "SELECT * FROM data WHERE sample_index >= 1 AND sample_index < 3"
            ),
            id="tiny-change-middle",
            description="Second and third samples",
        )
        everything = taco.export(f"{base}/folder", output / "copy.zip")

    assert subset.samples == 2
    assert taco.validate(output / "middle.zip").ok
    with zipfile.ZipFile(output / "middle.zip") as archive_file:
        assert archive_file.read("DATA/0/mask.tif") == b"1:mask.tif"
        assert archive_file.read("DATA/1/extra1.png") == b"2:extra1.png"

    assert everything.samples == 4
    assert taco.validate(output / "copy.zip").ok
    unpacked = output / "unpacked"
    with zipfile.ZipFile(output / "copy.zip") as archive_file:
        archive_file.extractall(unpacked)
    assert data_files(unpacked) == data_files(folder_dataset)


@pytest.mark.skipif(
    os.environ.get("TACO_TEST_REMOTE") != "1",
    reason="set TACO_TEST_REMOTE=1 to export from Hugging Face and Source Coop",
)
def test_export_remote_subsets(tmp_path: Path) -> None:
    archive = "hf://datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/single-zip/dataset.zip"
    catalog = "source://asterisk-labs/taco-api-fixtures/data/04-change-detection/by-split/.tacocat"

    first = taco.export(
        archive,
        tmp_path / "hf.zip",
        samples=taco.open_dataset(archive).sql("SELECT * FROM data WHERE sample_index < 2"),
        id="change-detection-mini",
        description="Two samples",
    )
    assert first.samples == 2
    assert taco.validate(tmp_path / "hf.zip").ok
    assert taco.read(tmp_path / "hf.zip").num_rows == 2

    test_split = taco.export(
        catalog,
        tmp_path / "test",
        samples=taco.read(catalog).filter(pc.field("ml:split") == "test"),
        id="change-detection-test",
        description="Test samples",
    )
    assert test_split.samples == 2
    assert taco.validate(tmp_path / "test").ok
    assert all(path.stat().st_size > 0 for path in (tmp_path / "test" / "DATA").rglob("*") if path.is_file())
