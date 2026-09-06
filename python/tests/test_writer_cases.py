from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pytest

import taco
from taco._view import DatasetView, open_view
from taco.contract.naming import CURRENT_ID

from .datasets import CASES, DatasetCase, case_id, get_case


def write_case(
    case: DatasetCase,
    path: Path,
    *,
    samples: tuple[taco.Sample, ...] | None = None,
    version: str | None = None,
    append: bool = False,
    batch_size: int = 2,
):
    collection = case.collection if version is None else case.collection.replace(dataset_version=version)
    with taco.open_writer(collection, path, append=append, batch_size=batch_size) as writer:
        writer.extend(case.samples if samples is None else samples)
        return writer.run()


def assert_schema_matches(left: pa.Table, right: pa.Table) -> None:
    assert left.schema.metadata == right.schema.metadata
    assert left.column_names == right.column_names
    for left_field, right_field in zip(left.schema, right.schema, strict=True):
        assert left_field.type == right_field.type
        assert left_field.nullable == right_field.nullable
        assert left_field.metadata == right_field.metadata


def assert_same_folder(left: DatasetView, right: DatasetView) -> None:
    assert left.collection_json == right.collection_json
    assert left.levels == right.levels
    for level in left.levels:
        left_table = left.level(level)
        right_table = right.level(level)
        assert_schema_matches(left_table, right_table)
        assert left_table.to_pylist() == right_table.to_pylist()


def data_files(path: Path) -> dict[str, bytes]:
    root = path / "DATA"
    return {file.relative_to(root).as_posix(): file.read_bytes() for file in root.rglob("*") if file.is_file()}


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_contract_cases(case: DatasetCase) -> None:
    assert case.collection.contract.levels == case.levels
    assert len(case.row_counts) == len(case.levels)
    assert len(case.samples) == len(case.files)
    assert taco.Contract.from_dict(case.collection.contract.to_dict()) == case.collection.contract
    for sample in case.samples:
        case.collection.contract.validate_sample(sample)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_writer_cases_match_across_containers(case: DatasetCase, tmp_path: Path) -> None:
    folder_path = tmp_path / case.name
    zip_path = tmp_path / f"{case.name}.zip"
    folder_result = write_case(case, folder_path)
    zip_result = write_case(case, zip_path)

    assert folder_result.samples == zip_result.samples == len(case.samples)
    assert folder_result.data_files == zip_result.data_files == len(case.data_paths)
    assert taco.validate(folder_path).ok
    assert taco.validate(zip_path).ok

    folder = open_view(folder_path)
    archive = open_view(zip_path)
    assert folder.collection_json == archive.collection_json
    assert folder.levels == archive.levels == case.levels
    assert folder.sample_count == archive.sample_count == len(case.samples)

    for level, row_count in zip(case.levels, case.row_counts, strict=True):
        folder_table = folder.level(level)
        archive_table = archive.level(level)
        assert folder_table.num_rows == archive_table.num_rows == row_count
        assert folder_table.schema.metadata == archive_table.schema.metadata == {b"taco:level": level.encode()}
        assert archive_table.select(folder_table.column_names).to_pylist() == folder_table.to_pylist()
        assert folder_table.column(CURRENT_ID).to_pylist() == list(range(folder_table.num_rows))
        assert archive_table.column(CURRENT_ID).to_pylist() == list(range(archive_table.num_rows))

    expected = sorted(case.data_paths)
    assert sorted(row.relative_path for row in folder.iter_data_rows()) == expected
    assert sorted(row.relative_path for row in archive.iter_data_rows()) == expected
    assert sorted(data_files(folder_path)) == expected

    raw = zip_path.read_bytes()
    for row in archive.iter_data_rows():
        assert isinstance(row.offset, int)
        assert row.offset >= 0
        assert isinstance(row.size, int)
        assert row.size > 0
        assert raw[row.offset : row.offset + row.size] == (folder_path / "DATA" / row.relative_path).read_bytes()

    assert taco.reader.read(folder_path).num_rows == len(case.samples)
    assert taco.reader.read(zip_path).num_rows == len(case.samples)
    assert taco.reader.read(folder_path, pivoted=False).num_rows == len(case.data_paths)
    assert taco.reader.read(zip_path, pivoted=False).num_rows == len(case.data_paths)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_append_matches_one_pass_write(case: DatasetCase, tmp_path: Path) -> None:
    split = max(1, len(case.samples) // 2)
    direct_path = tmp_path / "direct"
    append_path = tmp_path / "append"

    write_case(case, direct_path, version="1.1.0", batch_size=100)
    write_case(case, append_path, samples=case.samples[:split], batch_size=1)
    write_case(
        case,
        append_path,
        samples=case.samples[split:],
        version="1.1.0",
        append=True,
        batch_size=2,
    )

    direct = open_view(direct_path)
    appended = open_view(append_path)
    assert_same_folder(direct, appended)
    assert data_files(direct_path) == data_files(append_path)
    assert taco.validate(append_path).ok


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_partitioned_writer_cases(case: DatasetCase, tmp_path: Path) -> None:
    output = tmp_path / f"{case.name}.zip"
    with taco.open_writer(case.collection, output, partition_size=1, batch_size=2) as writer:
        writer.extend(case.samples)
        result = writer.run()

    assert result.samples == len(case.samples)
    assert result.data_files == len(case.data_paths)
    assert len(result.parts) == len(case.samples)
    assert taco.validate(result.path).ok
    assert all(taco.validate(part).ok for part in result.parts)
    dataset = open_view(result.path)
    assert dataset.levels == case.levels
    for level, row_count in zip(case.levels, case.row_counts, strict=True):
        assert dataset.level(level).num_rows == row_count
    assert taco.reader.read(result.path).num_rows == len(case.samples)
    assert taco.reader.read(result.path, pivoted=False).num_rows == len(case.data_paths)


def test_derived_metadata_is_batch_invariant(tmp_path: Path) -> None:
    case = get_case("derived_metadata")
    tables = []
    for batch_size in (1, 2, 100):
        path = tmp_path / str(batch_size)
        write_case(case, path, batch_size=batch_size)
        tables.append(open_view(path).level("sample").select(["stac:cloud_cover", "majortom:code"]))

    assert tables[0].to_pylist() == tables[1].to_pylist() == tables[2].to_pylist()


def test_child_rows_follow_contract_order(tmp_path: Path) -> None:
    case = get_case("nested_folders")
    path = tmp_path / "ordered"
    write_case(case, path)
    dataset = open_view(path)

    assert dataset.level("children").column("internal:relative_path").to_pylist() == [
        "0/before",
        "0/after",
        "0/change_map.tif",
        "1/before",
        "1/after",
        "1/change_map.tif",
    ]
    assert dataset.level("children/before").column("internal:relative_path").to_pylist() == [
        "0/before/B02.tif",
        "0/before/B03.tif",
        "0/before/B04.tif",
        "1/before/B02.tif",
        "1/before/B03.tif",
        "1/before/B04.tif",
    ]


def public_metadata_groups(module: ModuleType) -> set[type[object]]:
    groups = set()
    for name in module.__all__:
        value = getattr(module, name)
        if isinstance(value, type) and getattr(value, "__taco_scopes__", frozenset()):
            groups.add(value)
    return groups


def test_every_public_metadata_group_has_a_writer_case() -> None:
    modules = (
        taco.metadata.sample,
        taco.metadata.folder,
        taco.metadata.asset,
        taco.metadata.collection,
    )
    public = set().union(*(public_metadata_groups(module) for module in modules))
    used: set[type[object]] = set()

    for case in CASES:
        for groups in case.collection.contract._groups.values():
            for group in groups:
                target = group.model if group.model is not None else type(group.derived)
                used.update(candidate for candidate in public if issubclass(target, candidate))
        if case.collection.metadata is not None:
            for model in case.collection.metadata.groups.values():
                used.update(candidate for candidate in public if isinstance(model, candidate))

    external = {taco.metadata.sample.GeoEnrich}
    assert public == used | external
