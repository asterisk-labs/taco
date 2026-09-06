from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import pyarrow as pa
import pytest
from pydantic import BaseModel, Field

import taco
from taco.errors import ContractError, SampleError


class Values(BaseModel):
    count: Annotated[int, pa.int32()] = Field(description="Count")
    score: float | None = None


def test_model_schema_is_serialized() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=taco.MetadataSchema(taco.Level("sample", values=Values)),
    )
    assert contract.levels == ("sample", "children")
    assert contract.to_dict()["taco:metadata"]["sample"] == {
        "values:count": {"type": "int32", "nullable": False, "description": "Count"},
        "values:score": {"type": "double", "nullable": True, "description": ""},
    }
    assert taco.Contract.from_dict(contract.to_dict()) == contract


def test_serialized_schema_uses_the_complete_form() -> None:
    contract = taco.Contract(structure=["a.bin"])
    data = contract.to_dict()
    del data["taco:metadata"]["children"]
    with pytest.raises(ContractError, match="missing levels"):
        taco.Contract.from_dict(data)

    data = contract.to_dict()
    data["taco:metadata"]["sample"]["x:value"] = "int64"
    with pytest.raises(ContractError, match="type, nullable, and description"):
        taco.Contract.from_dict(data)


def test_optional_group_makes_all_fields_nullable() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=taco.MetadataSchema(taco.Level("children", values=Values | None)),
    )
    assert all(field.nullable for field in contract.metadata["children"].values())
    contract.validate_sample(taco.Sample(assets=[taco.Asset(b"x", path="a.bin")]))


def test_required_group_is_checked() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=taco.MetadataSchema(taco.Level("sample", values=Values)),
    )
    with pytest.raises(SampleError, match="required"):
        contract.validate_sample(taco.Sample(assets=[taco.Asset(b"x", path="a.bin")]))


def test_assets_infer_flat_paths(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    contract = taco.Contract(structure=["a.bin"])
    sample = contract.validate_sample(taco.Sample(assets=[taco.Asset(source)]))
    assert sample.assets[0].path == "a.bin"


def test_nested_and_renamed_assets_need_path(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    with pytest.raises(SampleError, match="pass path"):
        taco.Contract(structure=["folder/a.bin"]).validate_sample(taco.Sample(assets=[taco.Asset(source)]))
    with pytest.raises(SampleError, match="pass path"):
        taco.Contract(structure=["b.bin"]).validate_sample(taco.Sample(assets=[taco.Asset(source)]))


def test_variable_leaf_allows_empty_sample() -> None:
    contract = taco.Contract(structure=["image*[0,3].tif"])
    contract.validate_sample(taco.Sample())
    with pytest.raises(SampleError, match="contiguous"):
        contract.validate_sample(taco.Sample(assets=[taco.Asset(b"x", path="image1.tif")]))


@pytest.mark.parametrize(
    "structure",
    [
        [],
        ["../a.bin"],
        ["a__b.bin"],
        ["bad[folder]/a.bin"],
        ["a.bin", "a.bin"],
        ["x*[2,1].bin"],
        ["x*[0,0].bin"],
        ["x*[0,2].bin", "x0.bin"],
        ["x*[0,2]1", "x1*[0,2]"],
        ["x*[0,2]", "x0/a.bin"],
    ],
)
def test_invalid_structures(structure: list[str]) -> None:
    with pytest.raises(ContractError):
        taco.Contract(structure=structure)


def test_disjoint_variable_leaves_with_similar_names() -> None:
    taco.Contract(structure=["x*[0,2]1", "x1*[0,1]"])
    taco.Contract(structure=["x*[0,2]", "x9"])
    taco.Contract(structure=["x*[0,2]", "x9/a.bin"])


def test_scope_is_enforced() -> None:
    with pytest.raises(ContractError, match="cannot be used"):
        taco.Contract(
            structure=["a.tif"],
            metadata=taco.MetadataSchema(taco.Level("sample", raster=taco.metadata.asset.Raster)),
        )
    with pytest.raises(TypeError, match="not collection"):
        taco.CollectionMetadata(split=taco.metadata.sample.Split(split="train"))
    with pytest.raises(ContractError, match="cannot be used"):
        taco.Contract(
            structure=["folder/a.tif"],
            metadata=taco.MetadataSchema(taco.Level("children", stac=taco.metadata.sample.STAC | None)),
        )
    taco.Contract(
        structure=["folder/a.tif"],
        metadata=taco.MetadataSchema(taco.Level("children", stac=taco.metadata.folder.STAC)),
    )


def test_derived_declaration_and_dependency() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=taco.MetadataSchema(
            taco.Level("sample", stac=taco.metadata.sample.STAC, grid=taco.metadata.sample.MajorTOM(50))
        ),
    )
    descriptor = contract.to_dict()["taco:derived"]["sample"]["grid"]
    assert descriptor["requires"] == ["stac:centroid"]
    assert descriptor["produces"] == ["grid:code"]
    assert descriptor["configuration"]["dist_km"] == 50

    with pytest.raises(ContractError, match="missing"):
        taco.Contract(
            structure=["a.bin"],
            metadata=taco.MetadataSchema(taco.Level("sample", grid=taco.metadata.sample.MajorTOM())),
        )


def test_serialized_derived_declaration_is_validated() -> None:
    metadata = {
        "sample": {
            "a:value": "int64",
            "b:value": "int64",
        },
    }
    with pytest.raises(ContractError, match="missing"):
        taco.Contract(
            structure=None,
            metadata=metadata,
            derived={
                "sample": {
                    "a": {"requires": ["missing:value"], "produces": ["a:value"]},
                }
            },
        )
    with pytest.raises(ContractError, match="cycle"):
        taco.Contract(
            structure=None,
            metadata=metadata,
            derived={
                "sample": {
                    "a": {"requires": ["b:value"], "produces": ["a:value"]},
                    "b": {"requires": ["a:value"], "produces": ["b:value"]},
                }
            },
        )
    with pytest.raises(ContractError, match="more than once"):
        taco.Contract(
            structure=None,
            metadata=metadata,
            derived={
                "sample": {
                    "a": {"requires": [], "produces": ["a:value", "a:value"]},
                }
            },
        )


def test_derived_configuration_must_be_json() -> None:
    with pytest.raises(ContractError, match="JSON serializable"):
        taco.Contract(
            structure=None,
            metadata={"sample": {"a:value": "int64"}},
            derived={
                "sample": {
                    "a": {
                        "requires": [],
                        "produces": ["a:value"],
                        "configuration": {"value": float("nan")},
                    }
                }
            },
        )


def test_custom_types() -> None:
    class Types(BaseModel):
        at: Annotated[datetime, pa.timestamp("ms", tz="UTC")]
        values: list[int]

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", custom=Types)),
    )
    fields = contract.metadata["sample"]
    assert fields["custom:at"].type == "timestamp[ms, UTC]"
    assert fields["custom:values"].type == "list<int64>"
