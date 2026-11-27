from __future__ import annotations

import json
import math
import shutil
import struct
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pydantic import BaseModel, Field

import taco
from taco.container import cozip_plan, cozip_write
from taco.container.view import open_view
from taco.contract import Extent


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


def test_undeclared_folder_file_is_an_error(folder_dataset: Path) -> None:
    extra = folder_dataset / "DATA/0/extra.txt"
    extra.write_text("not declared")
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "data" and "not described" in issue.message for issue in report.errors)


def test_bad_current_ids_are_reported(folder_dataset: Path) -> None:
    path = folder_dataset / "METADATA/sample.parquet"
    table = pq.read_table(path)
    table = table.set_column(0, table.schema.field(0), pa.array([4, 1, 2, 3], type=pa.uint64()))
    pq.write_table(table, path)
    report = taco.validate(folder_dataset)
    assert not report.ok
    assert any(issue.code == "current_id" for issue in report.errors)


def test_invalid_sample_ids_are_reported(folder_dataset: Path) -> None:
    path = folder_dataset / "METADATA/sample.parquet"
    table = pq.read_table(path)
    index = table.schema.get_field_index("id")
    field = table.schema.field(index).with_nullable(True)
    table = table.set_column(index, field, pa.array(["same", " ", None, "same"], type=pa.string()))
    pq.write_table(table, path)

    messages = [issue.message for issue in taco.validate(folder_dataset).errors if issue.code == "id"]
    assert any("null on 1 rows" in message for message in messages)
    assert any("empty on 1 rows" in message for message in messages)
    assert any("1 duplicates" in message for message in messages)


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
    index = table.schema.get_field_index("info:split")
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


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("stac:proj_shape", [-1, 256]),
        ("stac:proj_transform", [0, 0, 0, 0, 0, 0]),
        ("stac:proj_code", "not-a-code"),
    ],
)
def test_invalid_stored_spatial_fields_are_reported(folder_dataset: Path, column: str, value: object) -> None:
    path = folder_dataset / "METADATA/sample.parquet"
    table = pq.read_table(path)
    field = table.schema.field(column)
    values = table.column(column).to_pylist()
    values[0] = value
    table = table.set_column(table.schema.get_field_index(column), field, pa.array(values, type=field.type))
    pq.write_table(table, path)
    report = taco.validate(folder_dataset, check_data=False)
    assert any(issue.code == "profile" for issue in report.errors)


@pytest.fixture
def catalog(tmp_path: Path, collection: taco.Collection, make_sample) -> Path:
    parts = []
    for name, indexes in (("a.zip", (0, 1)), ("b.zip", (2,))):
        with taco.open_writer(collection, tmp_path / name) as writer:
            writer.extend(make_sample(index) for index in indexes)
            writer.run()
        parts.append(tmp_path / name)
    return taco.consolidate(parts)


def edit_sources(catalog: Path, edit: Callable[[dict[str, Any]], object]) -> None:
    file = catalog / "COLLECTION.json"
    data = json.loads(file.read_text())
    edit(data)
    file.write_text(json.dumps(data))


def bump(catalog: Path, column: str) -> None:
    path = catalog / "children.parquet"
    table = pq.read_table(path)
    values = table.column(column).to_pylist()
    values[next(row for row, value in enumerate(values) if value is not None)] += 1
    index = table.schema.get_field_index(column)
    field = table.schema.field(index)
    pq.write_table(table.set_column(index, field, pa.array(values, type=field.type)), path)


@pytest.mark.parametrize(
    ("tamper", "code", "message"),
    [
        pytest.param(lambda cat: bump(cat, "internal:offset"), "sources", "['internal:offset']", id="offset"),
        pytest.param(lambda cat: bump(cat, "internal:size"), "sources", "['internal:size']", id="size"),
        pytest.param(
            lambda cat: edit_sources(cat, lambda d: d["taco:sources"]["partitions"][0].update(spatial=[0, 0, 1, 1])),
            "sources",
            "spatial and temporal for 'a.zip'",
            id="partition-spatial",
        ),
        pytest.param(
            lambda cat: edit_sources(cat, lambda d: d["taco:sources"]["partitions"][1].pop("temporal")),
            "sources",
            "spatial and temporal for 'b.zip'",
            id="partition-temporal",
        ),
        pytest.param(
            lambda cat: edit_sources(cat, lambda d: d["extent"].update(spatial=[0, 0, 1, 1])),
            "sources",
            "union of the partition extents",
            id="extent",
        ),
        pytest.param(
            lambda cat: edit_sources(cat, lambda d: d.update(id="other")), "sources", "in ['id']", id="collection"
        ),
        pytest.param(
            lambda cat: edit_sources(cat, lambda d: d["taco:sources"]["partitions"].reverse()),
            "sources",
            "in taco:sources order",
            id="partition-order",
        ),
        pytest.param(lambda cat: (cat / "notes.txt").write_text("x"), "metadata", "['notes.txt']", id="extra-file"),
        pytest.param(lambda cat: (cat.parent / "b.zip").unlink(), "sources", "cannot be found", id="missing-partition"),
        pytest.param(
            lambda cat: shutil.copyfile(cat.parent / "a.zip", cat.parent / "b.zip"),
            "sources",
            "row count of 'b.zip'",
            id="replaced-partition",
        ),
    ],
)
def test_catalog_must_match_its_partitions(
    catalog: Path, tamper: Callable[[Path], object], code: str, message: str
) -> None:
    assert taco.validate(catalog).ok
    tamper(catalog)
    report = taco.validate(catalog)
    assert any(issue.code == code and message in issue.message for issue in report.errors), report


def test_partitions_are_validated_as_archives(catalog: Path) -> None:
    part = catalog.parent / "b.zip"
    data = bytearray(part.read_bytes())
    with zipfile.ZipFile(part) as archive:
        local = archive.getinfo("DATA/0/mask.tif").header_offset
    name_length, extra_length = struct.unpack_from("<HH", data, local + 26)
    data[local + 30 + name_length + extra_length] ^= 1
    part.write_bytes(data)

    report = taco.validate(catalog)
    assert any(
        issue.code == "zip" and issue.message.startswith("partition 'b.zip': payloads do not match their CRC-32")
        for issue in report.errors
    ), report
    assert not any("CRC-32" in issue.message for issue in taco.validate(catalog, check_data=False).errors)


class Reading(BaseModel):
    value: float = Field(description="Sensor reading")


def test_catalog_with_nan_values_is_valid(tmp_path: Path) -> None:
    collection = taco.Collection(
        contract=taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", reading=Reading)]),
        id="readings",
        description="Readings",
        licenses=["MIT"],
        providers=["me"],
    )
    parts = []
    for name, indexes in (("a.zip", range(2)), ("b.zip", range(2, 4))):
        with taco.open_writer(collection, tmp_path / name) as writer:
            writer.extend(
                taco.Sample(id=f"r{index}", assets=b"x", metadata=taco.Metadata(reading=Reading(value=math.nan)))
                for index in indexes
            )
            writer.run()
        parts.append(tmp_path / name)
    report = taco.validate(taco.consolidate(parts))
    assert report.ok, report


def patch_collection(dataset: Path, edit: Callable[[dict[str, Any]], object], raw: str = "") -> None:
    """Edit COLLECTION.json; the value "<raw>" becomes the JSON text raw."""
    file = dataset / "COLLECTION.json"
    data = json.loads(file.read_text())
    edit(data)
    file.write_text(json.dumps(data).replace('"<raw>"', raw))


def repack(archive: Path, output: Path, edit: Callable[[Path], object]) -> Path:
    """Unpack a TACO ZIP, edit its files and pack them again in the CoZIP layout."""
    stage = output.parent / f"{output.stem}-files"
    with zipfile.ZipFile(archive) as source:
        names = [name for name in source.namelist() if not name.startswith("__cozip")]
        source.extractall(stage, names)
    edit(stage)
    data = [(name, stage / name) for name in names if name.startswith("DATA/")]
    priority = [name for name in names if not name.startswith("DATA/")]
    cozip_write(output, cozip_plan(data, priority), [(name, stage / name) for name in priority])
    return output


@pytest.mark.parametrize(
    ("edit", "raw"),
    [
        pytest.param(lambda data: data.update(title="<raw>"), "NaN", id="nan"),
        pytest.param(lambda data: data["providers"][0].update(links=[{"href": "<raw>"}]), "-Infinity", id="links"),
        pytest.param(lambda data: data.update({"taco:structure": dict.fromkeys(data["taco:structure"])}), "", id="map"),
    ],
)
def test_collection_json_the_native_reader_rejects_is_invalid(
    folder_dataset: Path, edit: Callable[[dict[str, Any]], object], raw: str
) -> None:
    patch_collection(folder_dataset, edit, raw)
    with pytest.raises(taco.errors.ContainerError):
        taco.open_dataset(folder_dataset)
    report = taco.validate(folder_dataset)
    assert [issue.code for issue in report.errors] == ["container"], report


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(lambda data: data.update(title=123), "title must be a string", id="title"),
        pytest.param(lambda data: data["providers"][0].update(url=5), "provider url", id="provider-url"),
        pytest.param(lambda data: data.update(curators=[{"name": 7}]), "curator name must be a string", id="curator"),
        pytest.param(lambda data: data["extent"].update(spatial=["-77", "-13", "-72", "-11"]), "numbers", id="extent"),
    ],
)
def test_collection_json_values_of_the_wrong_type_are_reported(
    folder_dataset: Path, edit: Callable[[dict[str, Any]], object], message: str
) -> None:
    patch_collection(folder_dataset, edit)
    report = taco.validate(folder_dataset)
    assert [issue.code for issue in report.errors] == ["container"], report
    assert message in report.errors[0].message


@pytest.mark.parametrize(
    ("key", "code", "message"),
    [
        ("taco:extensions", "extensions", "must list the extensions"),
        # The native reader takes the key for a TACOCAT, whatever its value.
        ("taco:sources", "sources", "only valid in TACOCAT"),
    ],
)
def test_null_collection_keys_are_reported(folder_dataset: Path, key: str, code: str, message: str) -> None:
    patch_collection(folder_dataset, lambda data: data.update({key: None}))
    report = taco.validate(folder_dataset)
    assert any(issue.code == code and message in issue.message for issue in report.errors), report


@pytest.mark.parametrize(
    ("level", "column"),
    [("sample", "internal:current_id"), ("children", "internal:current_id"), ("children", "internal:relative_path")],
)
def test_parent_levels_without_internal_columns_are_reported(folder_dataset: Path, level: str, column: str) -> None:
    path = folder_dataset / "METADATA" / f"{level}.parquet"
    pq.write_table(pq.read_table(path).drop_columns([column]), path)
    report = taco.validate(folder_dataset)
    assert any(issue.code == "schema" and f"missing columns ['{column}']" in issue.message for issue in report.errors)


def test_null_relative_path_in_an_archive_is_reported(archive: Path, tmp_path: Path) -> None:
    def edit(stage: Path) -> None:
        path = stage / "METADATA" / "children.parquet"
        table = pq.read_table(path)
        index = table.schema.get_field_index("internal:relative_path")
        values = [None, *table.column(index).to_pylist()[1:]]
        field = table.schema.field(index).with_nullable(True)
        pq.write_table(table.set_column(index, field, pa.array(values, pa.string())), path)

    assert taco.validate(repack(archive, tmp_path / "same.zip", lambda stage: None)).ok
    report = taco.validate(repack(archive, tmp_path / "null-path.zip", edit))
    assert any(issue.code == "schema" and "contains null values" in issue.message for issue in report.errors), report


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            lambda data: data["extent"].update(spatial=[0, 0, 1, 1]), "does not match the stored rows", id="box"
        ),
        pytest.param(
            lambda data: data["extent"].update(temporal=["1990-01-01T00:00:00Z", "1990-01-02T00:00:00Z"]),
            "does not match the stored rows",
            id="time",
        ),
        pytest.param(lambda data: data["extent"].update(temporal=None), "does not match the stored rows", id="no-time"),
        pytest.param(lambda data: data.pop("extent"), "extent is missing", id="missing"),
    ],
)
def test_extent_must_summarize_the_rows(
    folder_dataset: Path, edit: Callable[[dict[str, Any]], object], message: str
) -> None:
    assert taco.validate(folder_dataset).ok
    patch_collection(folder_dataset, edit)
    report = taco.validate(folder_dataset)
    assert [(issue.code, message in issue.message) for issue in report.errors] == [("extensions", True)], report


def test_archive_and_partition_extents_are_checked(catalog: Path, tmp_path: Path) -> None:
    def shrink(stage: Path) -> None:
        patch_collection(stage, lambda data: data["extent"].update(spatial=[0, 0, 1, 1]))

    report = taco.validate(repack(catalog.parent / "b.zip", tmp_path / "b-shrunk.zip", shrink))
    assert any(issue.message.startswith("extent ") for issue in report.errors), report
    shutil.move(tmp_path / "b-shrunk.zip", catalog.parent / "b.zip")
    report = taco.validate(catalog)
    assert any(issue.message.startswith("partition 'b.zip': extent ") for issue in report.errors), report


def test_extent_without_located_rows_must_be_omitted(tmp_path: Path) -> None:
    collection = taco.Collection(
        contract=taco.Contract(structure=["a.bin"]),
        id="plain",
        description="No profile",
        licenses=["MIT"],
        providers=["me"],
    )
    with taco.open_writer(collection, tmp_path / "plain") as writer:
        writer.add(taco.Sample(id="s0", assets=b"x"))
        writer.run()
    patch_collection(tmp_path / "plain", lambda data: data.update(extent={"spatial": [0, 0, 1, 1], "temporal": None}))
    report = taco.validate(tmp_path / "plain")
    assert any(issue.code == "extensions" and "must be omitted" in issue.message for issue in report.errors), report


def test_extent_comparison_allows_for_proj_digits(folder_dataset: Path) -> None:
    def shift_west(by: float) -> None:
        patch_collection(folder_dataset, lambda data: data["extent"]["spatial"].__setitem__(0, west + by))

    west = open_view(folder_dataset).collection_json["extent"]["spatial"][0]
    shift_west(1e-12)
    assert taco.validate(folder_dataset).ok
    shift_west(1e-6)
    assert not taco.validate(folder_dataset).ok


STAC = taco.extensions.sample.stac.STAC
NOON = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)


def write_boxes(path: Path, *boxes: tuple[float, float, float, float]) -> Path:
    """One sample per footprint, named after its bounds so that partitions never share an id."""
    contract = taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", stac=STAC)])
    collection = taco.Collection(contract=contract, id="boxes", description="Boxes", licenses=["MIT"], providers=["me"])
    with taco.open_writer(collection, path) as writer:
        for box in boxes:
            stac = STAC(geometry=shapely.box(*box).wkb, datetime=NOON)
            writer.add(taco.Sample(id="box" + "_".join(map(str, box)), assets=b"x", metadata=taco.Metadata(stac=stac)))
        writer.run()
    return path


def test_extent_west_may_name_the_antimeridian_either_way(tmp_path: Path) -> None:
    path = write_boxes(tmp_path / "west", (-180, 0, -170, 1))
    patch_collection(path, lambda data: data["extent"]["spatial"].__setitem__(0, 180))
    assert taco.validate(path).ok


def test_catalog_extent_breaks_ties_like_one_archive(tmp_path: Path) -> None:
    # Both covers of the two boxes leave out a 20 degree gap; the one that does not cross wins.
    west, east = (-170, 0, -10, 1), (10, 0, 170, 1)
    one = write_boxes(tmp_path / "one.zip", west, east)
    catalog = taco.consolidate([write_boxes(tmp_path / "a.zip", west), write_boxes(tmp_path / "b.zip", east)])
    expected = Extent((-170, 0, 170, 1), ("2024-01-01T12:00:00Z", "2024-01-01T12:00:00Z"))
    assert open_view(one).collection.extent == open_view(catalog).collection.extent == expected
    assert taco.validate(catalog).ok


def swap_rows(dataset: Path, level: str, first: str, second: str) -> None:
    """Swap two rows of a level, found by relative path, and number the rows again."""
    path = dataset / "METADATA" / f"{level.replace('/', '__')}.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    paths = [row["internal:relative_path"] for row in rows]
    one, other = paths.index(first), paths.index(second)
    rows[one], rows[other] = rows[other], rows[one]
    for position, row in enumerate(rows):
        row["internal:current_id"] = position
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)


STRUCTURE_ORDER = "the rows of 1 parent(s) do not follow the structure order"


@pytest.mark.parametrize(
    ("level", "first", "second", "expected"),
    [
        pytest.param("children", "0/mask.tif", "0/extra0.png", [("structure", STRUCTURE_ORDER)], id="fixed"),
        pytest.param("children", "2/extra0.png", "2/extra1.png", [("structure", STRUCTURE_ORDER)], id="sequence"),
        pytest.param(
            "children/before", "0/before/B02.tif", "0/before/B03.tif", [("structure", STRUCTURE_ORDER)], id="nested"
        ),
        pytest.param(
            "children",
            "0/extra0.png",
            "1/mask.tif",
            [("parent_id", "grouped by parent in parent order (first row 6)"), ("structure", STRUCTURE_ORDER)],
            id="parents",
        ),
    ],
)
def test_child_rows_follow_their_parents_and_the_structure(
    folder_dataset: Path, level: str, first: str, second: str, expected: list[tuple[str, str]]
) -> None:
    swap_rows(folder_dataset, level, first, second)
    report = taco.validate(folder_dataset)
    assert len(report.errors) == len(expected), report
    for (code, message), issue in zip(expected, report.errors, strict=True):
        assert issue.code == code, report
        assert issue.message.startswith(f"{level}: "), report
        assert message in issue.message, report


class Statistic(BaseModel):
    mean: float | None = Field(default=None, description="Mean of all valid values")


def restricted_dataset(path: Path, means: dict[str, float | None]) -> Path:
    """A level with rumi:mean restricted to a.rumi; it is written as x:mean, since writing Rumi needs rumi."""
    contract = taco.Contract(structure=["a.rumi", "b.rumi"], metadata=[taco.Level("children", x=Statistic)])
    collection = taco.Collection(contract=contract, id="stats", description="Stats", licenses=["MIT"], providers=["me"])
    with taco.open_writer(collection, path) as writer:
        assets = [
            taco.Asset(name.encode(), path=name, metadata=taco.Metadata(x=Statistic(mean=mean)))
            for name, mean in means.items()
        ]
        writer.add(taco.Sample(id="s0", assets=assets))
        writer.run()

    def restrict(data: dict[str, Any]) -> None:
        level = data["taco:metadata"]["children"]
        level["rumi:mean"] = {**level.pop("x:mean"), "files": ["a.rumi"]}
        data["taco:extensions"].append("https://asterisk.coop/taco/spec/extensions/rumi/v1.0.0/schema.json")

    patch_collection(path, restrict)
    parquet = path / "METADATA" / "children.parquet"
    table = pq.read_table(parquet)
    renamed = table.rename_columns(["rumi:mean" if name == "x:mean" else name for name in table.column_names])
    pq.write_table(renamed.replace_schema_metadata(table.schema.metadata), parquet)
    return path


def test_a_field_restricted_to_files_is_null_on_other_rows(tmp_path: Path) -> None:
    assert taco.validate(restricted_dataset(tmp_path / "null", {"a.rumi": 1.0, "b.rumi": None})).ok
    report = taco.validate(restricted_dataset(tmp_path / "value", {"a.rumi": 1.0, "b.rumi": 2.0}))
    assert [(issue.code, issue.message) for issue in report.errors] == [
        (
            "schema",
            "children: column 'rumi:mean' has values on 1 rows outside its files ['a.rumi'] (first row 1: '0/b.rumi')",
        )
    ], report
