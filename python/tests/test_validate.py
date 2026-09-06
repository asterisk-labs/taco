from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import taco


def test_missing_path_is_reported(tmp_path: Path) -> None:
    report = taco.validate(tmp_path / "missing")
    assert not report.ok
    assert report.errors[0].code == "container"


def test_check_data_false_keeps_structural_checks(archive: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
    report = taco.validate(broken, check_data=False)
    assert not report.ok
    assert any(issue.code == "zip" for issue in report.errors)


def test_bad_cozip_profile_is_reported(archive: Path, tmp_path: Path) -> None:
    broken = tmp_path / "wrong-profile.zip"
    content = bytearray(archive.read_bytes())
    content[57] = 1
    broken.write_bytes(content)
    report = taco.validate(broken, check_data=False)
    assert not report.ok
    assert any(issue.code == "cozip" for issue in report.errors)


def test_missing_folder_file(folder_dataset: Path) -> None:
    (folder_dataset / "DATA/0/mask.tif").unlink()
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "data" for issue in report.errors)


def test_missing_folder_file_is_skipped_when_requested(folder_dataset: Path) -> None:
    (folder_dataset / "DATA/0/mask.tif").unlink()
    assert taco.validate(folder_dataset, check_data=False).ok


def test_bad_current_ids_are_reported(folder_dataset: Path) -> None:
    path = folder_dataset / "METADATA/sample.parquet"
    table = pq.read_table(path)
    table = table.set_column(0, table.schema.field(0), pa.array([4, 1, 2, 3], type=pa.uint64()))
    pq.write_table(table, path)
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "current_id" for issue in report.errors)


def test_bad_parent_is_reported(folder_dataset: Path) -> None:
    path = folder_dataset / "METADATA/children.parquet"
    table = pq.read_table(path)
    parents = table.column("internal:parent_id").to_pylist()
    parents[0] = 999
    table = table.set_column(
        table.schema.get_field_index("internal:parent_id"),
        table.schema.field("internal:parent_id"),
        pa.array(parents, type=pa.uint64()),
    )
    pq.write_table(table, path)
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "parent_id" for issue in report.errors)


@pytest.mark.parametrize("sample", ["²", "00"])
def test_bad_sample_index_is_reported(folder_dataset: Path, sample: str) -> None:
    path = folder_dataset / "METADATA/children.parquet"
    table = pq.read_table(path)
    values = table.column("internal:relative_path").to_pylist()
    values[0] = f"{sample}/before"
    table = table.set_column(
        table.schema.get_field_index("internal:relative_path"),
        table.schema.field("internal:relative_path"),
        pa.array(values, type=pa.string()),
    )
    pq.write_table(table, path)
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "relative_path" for issue in report.errors)


def test_schema_metadata_is_checked(folder_dataset: Path) -> None:
    path = folder_dataset / "METADATA/sample.parquet"
    table = pq.read_table(path)
    fields = list(table.schema)
    index = table.schema.get_field_index("ml:split")
    field = fields[index]
    fields[index] = pa.field(field.name, field.type, nullable=not field.nullable, metadata={b"description": b"wrong"})
    schema = pa.schema(fields)
    pq.write_table(pa.Table.from_arrays(table.columns, schema=schema), path)
    report = taco.validate(folder_dataset)
    assert not report.ok
    messages = [issue.message for issue in report.errors if issue.code == "schema"]
    assert any("taco:level" in message for message in messages)
    assert any("description" in message for message in messages)
    assert any("nullability" in message for message in messages)


def test_missing_data_directory_is_reported(folder_dataset: Path) -> None:
    shutil.rmtree(folder_dataset / "DATA")
    report = taco.validate(folder_dataset, check_data=False)
    assert not report.ok
    assert any("missing DATA/" in issue.message for issue in report.errors)


def test_extra_metadata_file_is_reported(folder_dataset: Path) -> None:
    shutil.copyfile(
        folder_dataset / "METADATA/sample.parquet",
        folder_dataset / "METADATA/extra.parquet",
    )
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "metadata" and "extra.parquet" in issue.message for issue in report.errors)


def test_unknown_reserved_collection_key_is_reported(folder_dataset: Path) -> None:
    file = folder_dataset / "COLLECTION.json"
    data = json.loads(file.read_text())
    data["taco:wrong"] = True
    file.write_text(json.dumps(data))
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert report.errors[0].code == "container"


def test_validation_report_can_raise(folder_dataset: Path) -> None:
    shutil.rmtree(folder_dataset / "METADATA")
    report = taco.validate(folder_dataset)
    assert not report.ok
    with pytest.raises(Exception, match="not a valid TACO"):
        report.raise_for_errors()
