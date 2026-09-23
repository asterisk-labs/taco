from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Annotated

import pyarrow as pa
import pytest
from pydantic import BaseModel

import taco
from taco.errors import ContainerError


class Split(BaseModel):
    split: str
    n_images: Annotated[int, pa.int32()]


class Kind(BaseModel):
    kind: str


class Raster(BaseModel):
    resolution: Annotated[int, pa.int32()]


class LocationMetadata(BaseModel):
    location: str


def payload(tag: str, index: int) -> bytes:
    return f"{tag}-{index}:".encode() * 40


def collection(name: str, contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id=name,
        description=name,
        licenses=["MIT"],
        providers=[{"name": "TACO tests"}],
        tasks=["other"],
    )


def write(name: str, contract: taco.Contract, samples: list[taco.Sample], output: Path, **options) -> Path:
    with taco.open_writer(collection(name, contract), output, **options) as writer:
        writer.extend(samples)
        return writer.run().path


def nested_samples() -> list[taco.Sample]:
    return [
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(ml=Split(split="train" if index % 2 == 0 else "val", n_images=index)),
            folders=[
                taco.Folder("before", metadata=taco.Metadata(node=Kind(kind="imagery"))),
                taco.Folder("after", metadata=taco.Metadata(node=Kind(kind="imagery"))),
            ],
            assets=[
                taco.Asset(
                    payload("b02", index), path="before/B02.bin", metadata=taco.Metadata(raster=Raster(resolution=10))
                ),
                taco.Asset(
                    payload("b03", index), path="before/B03.bin", metadata=taco.Metadata(raster=Raster(resolution=10))
                ),
                taco.Asset(
                    payload("a02", index), path="after/B02.bin", metadata=taco.Metadata(raster=Raster(resolution=20))
                ),
                taco.Asset(
                    payload("change", index), path="change.bin", metadata=taco.Metadata(node=Kind(kind="label"))
                ),
            ],
        )
        for index in range(3)
    ]


NESTED = taco.Contract(
    structure=["before/B02.bin", "before/B03.bin", "after/B02.bin", "change.bin"],
    metadata=taco.MetadataSchema(
        taco.Level("sample", ml=Split),
        taco.Level("children", node=Kind),
        taco.Level("children/before", raster=Raster),
        taco.Level("children/after", raster=Raster),
    ),
)


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("core-reader")
    write("nested", NESTED, nested_samples(), root / "nested.zip")
    write("nested", NESTED, nested_samples(), root / "nested")

    variable = taco.Contract(
        structure=["img*[1,3].bin", "mask.bin"],
        metadata=taco.MetadataSchema(taco.Level("sample", ml=Split), taco.Level("children", node=Kind)),
    )
    samples = []
    for index in range(3):
        assets = [
            taco.Asset(
                payload(f"img{number}", index), path=f"img{number}.bin", metadata=taco.Metadata(node=Kind(kind="image"))
            )
            for number in range(index + 1)
        ]
        assets.append(
            taco.Asset(payload("mask", index), path="mask.bin", metadata=taco.Metadata(node=Kind(kind="label")))
        )
        samples.append(
            taco.Sample(
                id=f"s{index}",
                metadata=taco.Metadata(ml=Split(split="train", n_images=index + 1)),
                assets=assets,
            )
        )
    write("variable", variable, samples, root / "variable.zip")

    nested_variable = taco.Contract(structure=["before/img*[1,3].bin"])
    write(
        "nested-variable",
        nested_variable,
        [
            taco.Sample(
                id="s0",
                assets=[taco.Asset(payload(f"img{number}", 0), path=f"before/img{number}.bin") for number in range(2)],
            )
        ],
        root / "nested-variable.zip",
    )

    shadow = taco.Contract(
        structure=["before/B02.bin", "change.bin"],
        metadata=taco.MetadataSchema(
            taco.Level("sample", ml=Split),
            taco.Level("children", raster=Raster),
            taco.Level("children/before", raster=Raster),
        ),
    )
    samples = [
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(ml=Split(split="train", n_images=1)),
            folders=[taco.Folder("before", metadata=taco.Metadata(raster=Raster(resolution=1)))],
            assets=[
                taco.Asset(
                    payload("b02", index), path="before/B02.bin", metadata=taco.Metadata(raster=Raster(resolution=3))
                ),
                taco.Asset(
                    payload("change", index), path="change.bin", metadata=taco.Metadata(raster=Raster(resolution=2))
                ),
            ],
        )
        for index in range(2)
    ]
    write("shadow", shadow, samples, root / "shadow.zip")

    single = taco.Contract(structure=["data.bin"], metadata=taco.MetadataSchema(taco.Level("sample", ml=Split)))
    samples = [
        taco.Sample(
            id=f"s{index}",
            assets=payload("sample", index),
            metadata=taco.Metadata(ml=Split(split="train", n_images=index)),
        )
        for index in range(6)
    ]
    write("single", single, samples, root / "single.zip")

    (root / "catalog").mkdir()
    write("nested", NESTED, nested_samples(), root / "catalog" / "part.zip", partition_by="ml:split")
    return root


def by_sample(table: pa.Table) -> list[dict]:
    return sorted(
        table.to_pylist(), key=lambda row: (row.get("source_file") or "", row["sample_index"], row.get("path") or "")
    )


def test_long_rows_carry_their_own_and_ancestor_metadata(data: Path) -> None:
    table = taco.open_dataset(data / "nested.zip").sql("SELECT * FROM files")
    assert table.num_rows == 12
    assert table.column_names[:4] == ["sample_index", "id", "path", "taco:location"]
    assert table.column_names[4:] == sorted(table.column_names[4:])
    first = [row for row in by_sample(table) if row["sample_index"] == 0]
    assert [(row["path"], row["node:kind"], row["raster:resolution"]) for row in first] == [
        ("after/B02.bin", "imagery", 20),
        ("before/B02.bin", "imagery", 10),
        ("before/B03.bin", "imagery", 10),
        ("change.bin", "label", None),
    ]
    assert all(row["ml:split"] == "train" for row in first)


def test_wide_rows_have_a_location_per_leaf(data: Path) -> None:
    table = taco.read(data / "nested.zip")
    assert table.num_rows == 3
    assert table.column("sample_index").to_pylist() == [0, 1, 2]
    row = by_sample(table)[0]
    assert row["before__B02.bin::location"].startswith("/vsisubfile/")
    assert row["before__B02.bin::location"].endswith(str((data / "nested.zip").resolve()))
    assert "change.bin::location" not in taco.open_dataset(data / "nested.zip").sql("SELECT * FROM data").column_names


def test_generated_columns_do_not_collide_with_metadata(tmp_path: Path) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    contract = taco.Contract(
        structure=["image"],
        metadata=taco.MetadataSchema(taco.Level("sample", image=LocationMetadata)),
    )
    output = write(
        "column-collision",
        contract,
        [
            taco.Sample(
                id="s2",
                assets=taco.Asset(b"payload", path="image"),
                metadata=taco.Metadata(image=LocationMetadata(location="metadata-value")),
            )
        ],
        tmp_path / "collision.zip",
    )

    for table in (taco.read(output), engine.open_reader().execute(native_sql(output)).to_arrow_table()):
        assert table.column_names.count("image:location") == 1
        assert table.column("image:location").to_pylist() == ["metadata-value"]
        assert table.column("image::location")[0].as_py().startswith("/vsisubfile/")


def test_files_and_sql_relations(data: Path) -> None:
    path = data / "nested.zip"
    dataset = taco.open_dataset(path)
    assert taco.read(path, files=["change.bin"]).column_names[-1] == "change.bin::location"
    assert "before__B02.bin::location" not in taco.read(path, files=["change.bin"]).column_names
    assert set(dataset.sql("SELECT path FROM files WHERE path = 'change.bin'").column("path").to_pylist()) == {
        "change.bin"
    }
    assert dataset.sql("SELECT sample_index FROM data WHERE sample_index = 1").column("sample_index").to_pylist() == [1]
    assert sorted(
        dataset.sql("SELECT sample_index FROM data WHERE sample_index >= 1 AND sample_index < 3")
        .column("sample_index")
        .to_pylist()
    ) == [1, 2]

    level = dataset.sql("SELECT * FROM children__before")
    assert level.num_rows == 6
    assert sorted(level.column("internal:relative_path").to_pylist())[0] == "0/before/B02.bin"


def test_variable_leaves_are_ordered_lists(data: Path) -> None:
    path = data / "variable.zip"
    rows = by_sample(taco.read(path))
    assert [(row["ml:n_images"], len(row["img::location"])) for row in rows] == [(1, 1), (2, 2), (3, 3)]
    long = taco.open_dataset(path).sql("SELECT * FROM files")
    first_image = next(
        row["taco:location"] for row in long.to_pylist() if row["sample_index"] == 2 and row["path"] == "img0.bin"
    )
    assert rows[2]["img::location"][0] == first_image
    assert "img::location" not in taco.open_dataset(path).sql("SELECT * FROM data").column_names

    nested = taco.read(data / "nested-variable.zip")
    assert nested.column_names == ["sample_index", "id", "before__img::location"]
    assert len(nested.column("before__img::location")[0].as_py()) == 2


def test_a_redeclared_field_takes_the_deepest_value(data: Path) -> None:
    dataset = taco.open_dataset(data / "shadow.zip")
    rows = by_sample(dataset.sql("SELECT * FROM files"))
    assert [(row["sample_index"], row["path"], row["raster:resolution"]) for row in rows] == [
        (0, "before/B02.bin", 3),
        (0, "change.bin", 2),
        (1, "before/B02.bin", 3),
        (1, "change.bin", 2),
    ]
    raw = dataset.sql(
        'SELECT folder."raster:resolution" AS folder_resolution, '
        'asset."raster:resolution" AS asset_resolution '
        "FROM children AS folder JOIN children__before AS asset "
        'ON asset."internal:parent_id" = folder."internal:current_id"'
    )
    assert raw.to_pydict() == {"folder_resolution": [1, 1], "asset_resolution": [3, 3]}


def test_single_file_samples(data: Path) -> None:
    dataset = taco.open_dataset(data / "single.zip")
    table = dataset.read()
    assert table.num_rows == 6
    assert all(value.startswith("/vsisubfile/") for value in table.column("data.bin::location").to_pylist())
    files = dataset.sql("SELECT * FROM files ORDER BY sample_index")
    assert files.column("path").to_pylist() == ["data.bin"] * 6
    with pytest.raises(ContainerError, match="unknown structure leaf"):
        dataset.read(files=["change.bin"])


def test_folder_and_catalog_locations(data: Path) -> None:
    folder = by_sample(taco.read(data / "nested"))
    assert folder[0]["change.bin::location"] == f"{(data / 'nested').resolve()}/DATA/0/change.bin"
    assert taco.open_dataset(data / "nested").sql("SELECT * FROM files").num_rows == 12

    catalog = taco.read(data / "catalog" / ".tacocat")
    assert catalog.num_rows == 3
    assert set(catalog.column("source_file").to_pylist()) == {"part_train.zip", "part_val.zip"}
    locations = catalog.column("change.bin::location").to_pylist()
    assert all(value.split(",", 1)[1].startswith(str((data / "catalog").resolve()) + "/part_") for value in locations)


def test_contract_errors(data: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(data / "nested", broken)
    (broken / "COLLECTION.json").write_text("{ not json")
    with pytest.raises(ContainerError, match=r"COLLECTION\.json is not valid JSON"):
        taco.open_dataset(broken)

    old = tmp_path / "old"
    shutil.copytree(data / "nested", old)
    document = json.loads((old / "COLLECTION.json").read_text())
    document["taco:version"] = "2.0.0"
    (old / "COLLECTION.json").write_text(json.dumps(document))
    with pytest.raises(ContainerError, match="unsupported TACO version"):
        taco.reader.inspect.levels(old)


@pytest.mark.skipif(
    os.environ.get("TACO_TEST_REMOTE") != "1",
    reason="set TACO_TEST_REMOTE=1 to read from Hugging Face and Source Coop",
)
def test_remote_datasets() -> None:
    base = "hf://datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection"
    archive = taco.open_dataset(f"{base}/single-zip/dataset.zip").sql("SELECT * FROM files")
    assert archive.num_rows == 18
    assert (
        archive.column("taco:location")[0]
        .as_py()
        .endswith(",/vsihf/datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/single-zip/dataset.zip")
    )
    assert taco.read(f"{base}/by-split/.tacocat").num_rows == 6
    folder = taco.read(f"{base}/folder")
    assert folder.num_rows == 6
    assert (
        folder.column("change.rumi")[0]
        .as_py()
        .startswith("/vsihf/datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/folder/DATA/")
    )

    mirror = "source://asterisk-labs/taco-api-fixtures/data/04-change-detection"
    catalog = taco.open_dataset(f"{mirror}/by-split/.tacocat").sql("SELECT * FROM files")
    assert catalog.num_rows == 18
    location = catalog.column("taco:location")[0].as_py()
    assert ",/vsisource/asterisk-labs/taco-api-fixtures/data/04-change-detection/by-split/" in location
    assert taco.read(f"{mirror}/folder").num_rows == 6
