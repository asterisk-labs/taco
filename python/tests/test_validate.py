from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import taco
from taco.errors import ValidationFailed
from taco.validate import ValidationReport


def _codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.errors}


def test_valid_archive(archive: Path) -> None:
    report = taco.validate(archive)
    assert report.ok and report.container == "zip"
    assert "valid" in str(report)
    report.raise_for_errors()
    assert taco.validate(archive, check_data=False).ok


def test_missing_dataset(tmp_path: Path) -> None:
    report = taco.validate(tmp_path / "nope.zip")
    assert not report.ok and _codes(report) == {"container"}
    with pytest.raises(ValidationFailed):
        report.raise_for_errors()


def test_warnings_for_optional_fields(tmp_path: Path) -> None:
    contract = taco.Contract(structure=["a.bin"])
    collection = taco.Collection(
        contract=contract,
        id="w",
        dataset_version="0.1.0",
        description="d",
        licenses=["MIT"],
        providers=["p"],
        tasks=["weird-task"],
    )
    with taco.open_writer(collection, tmp_path / "w.zip") as writer:
        writer.add(taco.Sample(assets={"a.bin": b"x"}))
        result = writer.run()
    report = taco.validate(result.path)
    assert report.ok
    assert {issue.code for issue in report.warnings} == {"tasks", "title"}


def _rewrite_zip(source: Path, target: Path, mutate) -> Path:
    """Rebuild a plain (non-cozip) zip with modified entries for tamper tests."""
    with zipfile.ZipFile(source) as zf:
        entries = [(info.filename, zf.read(info.filename)) for info in zf.infolist()]
    entries = mutate(entries)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as out:
        for name, payload in entries:
            out.writestr(name, payload)
    return target


def _tamper_parquet(payload: bytes, mutate) -> bytes:
    table = pq.read_table(io.BytesIO(payload))
    table = mutate(table)
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def test_folder_tampering_is_detected(folder_dataset: Path) -> None:
    folder = folder_dataset
    assert taco.validate(folder).ok

    (folder / "DATA" / "0" / "mask.tif").unlink()
    (folder / "DATA" / "0" / "stray.txt").write_text("x")
    report = taco.validate(folder)
    assert "data" in _codes(report)
    assert any(issue.severity == "warning" and "stray" in issue.message for issue in report.issues)

    sample = folder / "METADATA" / "sample.parquet"
    table = pq.read_table(sample)
    shifted = table.set_column(0, "internal:current_id", pa.array(range(1, table.num_rows + 1), type=pa.uint64()))
    pq.write_table(shifted, sample)
    assert "current_id" in _codes(taco.validate(folder, check_data=False))

    broken = table.drop_columns(["kind"]).append_column("bogus", pa.array([1] * table.num_rows))
    pq.write_table(broken, sample)
    assert "schema" in _codes(taco.validate(folder, check_data=False))

    wrong_parent = table.set_column(1, "internal:parent_id", pa.array([99] * table.num_rows, type=pa.uint64()))
    pq.write_table(wrong_parent, sample)
    assert "parent_id" in _codes(taco.validate(folder, check_data=False))

    renamed = table.set_column(2, "internal:relative_path", pa.array(["0/nope"] * table.num_rows))
    pq.write_table(renamed, sample)
    codes = _codes(taco.validate(folder, check_data=False))
    assert "structure" in codes

    # A missing level file stops the reader before validation can look at it.
    sample.unlink()
    assert "container" in _codes(taco.validate(folder, check_data=False))
    (folder / "METADATA" / "extra.parquet").write_bytes(
        pq.read_table(folder / "METADATA" / "collection.parquet").to_pandas().to_parquet() if False else b""
    )
    (folder / "METADATA" / "extra.parquet").unlink()

    (folder / "COLLECTION.json").write_text("{not json")
    assert "container" in _codes(taco.validate(folder))


def test_zip_tampering_is_detected(tmp_path: Path, archive: Path) -> None:
    # Not a cozip at all
    plain = _rewrite_zip(archive, tmp_path / "plain.zip", lambda entries: [e for e in entries if e[0] != "__cozip__"])
    assert "container" in _codes(taco.validate(plain))

    # Offsets no longer match after rewriting the archive with a different layout
    raw = archive.read_bytes()
    shuffled = _rewrite_zip(archive, tmp_path / "shuffled.zip", lambda entries: [entries[0], *reversed(entries[1:])])
    # keep the original byte-0 index so open_dataset can still find the metadata
    with zipfile.ZipFile(shuffled) as zf:
        assert zf.namelist()[0] == "__cozip__"
    report = taco.validate(shuffled)
    assert not report.ok
    assert _codes(report) & {"container", "index", "zip", "offsets", "data"}

    # Corrupt the profile byte in place
    corrupted = tmp_path / "profile.zip"
    data = bytearray(raw)
    data[51 + 6] = 1
    corrupted.write_bytes(bytes(data))
    assert "container" in _codes(taco.validate(corrupted))


def test_tacocat_validation(tmp_path: Path, collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "ds.zip", partition_by="split") as writer:
        for index in range(4):
            writer.add(make_sample(index))
        result = writer.run()
    assert taco.validate(result.path).ok
    moved = tmp_path / "elsewhere"
    shutil.copytree(result.path, moved / ".tacocat")
    report = taco.validate(moved / ".tacocat")
    assert report.ok
    assert any(issue.code == "sources" for issue in report.warnings)

    parquet = moved / ".tacocat" / "sample.parquet"
    table = pq.read_table(parquet)
    pq.write_table(table.drop_columns(["internal:source_file"]), parquet)
    assert {"schema", "source_file"} & _codes(taco.validate(moved / ".tacocat"))

    catalog = json.loads((result.path / "COLLECTION.json").read_text())
    catalog["taco:sources"]["files"] = ["ds_train.zip"]
    (result.path / "COLLECTION.json").write_text(json.dumps(catalog))
    assert "source_file" in _codes(taco.validate(result.path))
