from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

import taco
from taco.contract.naming import partition_filename, sanitize_filename, validate_component


class Region(BaseModel):
    name: str | None = Field(description="Region the sample belongs to")


collection = taco.Collection(
    contract=taco.Contract(structure=["a.tif"], metadata=[taco.Level("sample", region=Region)]),
    id="regions",
    description="Samples partitioned by region",
    licenses=["MIT"],
    providers=["me"],
)


def partitioned(output: Path, values: list[str | None], **options: Any) -> taco.writer.base.BuildResult:
    with taco.open_writer(collection, output, partition_by="region:name", **options) as writer:
        for index, value in enumerate(values):
            metadata = taco.Metadata(region=Region(name=value))
            writer.add(taco.Sample(id=f"s{index}", assets=f"{value}".encode(), metadata=metadata))
        return writer.run()


@pytest.mark.parametrize(
    ("output", "values", "names"),
    [
        ("ds.zip", ["São Paulo", "Lima"], ["ds_Sao_Paulo.zip", "ds_Lima.zip"]),
        ("ds.zip", ["tile[1]", "tile[2]"], ["ds_tile_1.zip", "ds_tile_2.zip"]),
        # Case-insensitive file systems would store these as one file.
        ("ds.zip", ["Lima", "lima", "LIMA"], ["ds_Lima.zip", "ds_lima-2.zip", "ds_LIMA-3.zip"]),
        ("ds.zip", ["a b", "a_b"], ["ds_a_b.zip", "ds_a_b-2.zip"]),
        ("ds.zip", [None, "None"], ["ds_None.zip", "ds_None-2.zip"]),
        ("ds.zip", ["東京", "大阪"], ["ds_group.zip", "ds_group-2.zip"]),
        ("ds_.zip", ["north", "south"], ["ds_north.zip", "ds_south.zip"]),
        ("ds__v2.zip", ["north", "south"], ["ds_v2_north.zip", "ds_v2_south.zip"]),
        ("données[1].zip", ["north", "south"], ["donnees_1_north.zip", "donnees_1_south.zip"]),
        # Names that were valid keep their form.
        ("my data.zip", ["train", "v1.0", "a#b"], ["my data_train.zip", "my data_v1.0.zip", "my data_a#b.zip"]),
    ],
)
def test_partition_names_are_valid_and_unique(
    tmp_path: Path, output: str, values: list[str | None], names: list[str]
) -> None:
    result = partitioned(tmp_path / output, values)
    assert [part.name for part in result.parts] == names
    for name in names:
        validate_component(name, context="partition file name")
    assert taco.validate(result.path).ok
    assert taco.read(tmp_path).column("region:name").to_pylist() == values


def test_size_partitions_get_valid_names(tmp_path: Path) -> None:
    result = partitioned(tmp_path / "ds_.zip", ["Lima", "lima", "Lima"], partition_size=1)
    assert [part.name for part in result.parts] == [
        "ds_Lima_part0001.zip",
        "ds_Lima_part0002.zip",
        "ds_lima-2_part0001.zip",
    ]
    assert taco.validate(result.path).ok

    with taco.open_writer(collection, tmp_path / "sized" / "ds_.zip", partition_size=1) as writer:
        for index in range(2):
            writer.add(taco.Sample(id=f"s{index}", assets=b"x", metadata=taco.Metadata(region=Region(name="x"))))
        sized = writer.run()
    assert [part.name for part in sized.parts] == ["ds_part0001.zip", "ds_part0002.zip"]
    assert taco.validate(sized.path).ok


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("train", "train"),
        ("a  b", "a_b"),
        ("it's", "it_s"),
        ("Zürich", "Zurich"),
        ("tile[1]", "tile_1"),
        ("x\x01y", "x_y"),
        ("???", "group"),
    ],
)
def test_sanitize_filename(value: str, label: str) -> None:
    assert sanitize_filename(value) == label


def test_partition_filename_keeps_a_valid_stem() -> None:
    assert partition_filename("my data", "train") == "my data_train.zip"
    assert partition_filename("ds_", "train") == "ds_train.zip"
    assert partition_filename("___", "part0001") == "part0001.zip"
