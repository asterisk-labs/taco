from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pyarrow as pa
import pytest
from pydantic import BaseModel, Field

import taco
from taco.errors import CollectionError, ContractError, SampleError


class Values(BaseModel):
    count: Annotated[int, pa.int32()] = Field(description="Count")
    score: float | None = None


def test_model_schema_is_serialized() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=[taco.Level("sample", values=Values)],
    )
    assert contract.levels == ("sample", "children")
    assert contract.to_dict()["taco:metadata"]["sample"] == {
        "values:count": {"type": "int32", "nullable": False, "description": "Count"},
        "values:score": {"type": "double", "nullable": True, "description": ""},
    }
    assert taco.Contract.from_dict(contract.to_dict()) == contract


def test_metadata_takes_a_list_of_levels() -> None:
    empty = taco.Contract(structure=["a.bin"], metadata=[])
    assert empty == taco.Contract(structure=["a.bin"])
    with pytest.raises(ContractError, match="unique"):
        taco.Contract(
            structure=["a.bin"],
            metadata=[taco.Level("sample", values=Values), taco.Level("sample", other=Values)],
        )
    with pytest.raises(ContractError, match=r"list of taco\.Level"):
        taco.Contract(structure=["a.bin"], metadata=[{"sample": {}}])
    with pytest.raises(ContractError, match=r"list of taco\.Level"):
        taco.Contract(structure=["a.bin"], metadata=taco.Level("sample", values=Values))


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
        metadata=[taco.Level("children", values=Values | None)],
    )
    assert all(field.nullable for field in contract.metadata["children"].values())
    contract.validate_sample(taco.Sample(id="u0", assets=[taco.Asset(b"x", path="a.bin")]))


def test_required_group_is_checked() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=[taco.Level("sample", values=Values)],
    )
    with pytest.raises(SampleError, match="required"):
        contract.validate_sample(taco.Sample(id="u1", assets=[taco.Asset(b"x", path="a.bin")]))


def test_assets_infer_flat_paths(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    contract = taco.Contract(structure=["a.bin"])
    sample = contract.validate_sample(taco.Sample(id="u2", assets=[taco.Asset(source)]))
    assert sample.assets[0].path == "a.bin"


def test_nested_and_renamed_assets_need_path(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    with pytest.raises(SampleError, match="pass path"):
        taco.Contract(structure=["folder/a.bin"]).validate_sample(taco.Sample(id="u3", assets=[taco.Asset(source)]))
    with pytest.raises(SampleError, match="pass path"):
        taco.Contract(structure=["b.bin"]).validate_sample(taco.Sample(id="u4", assets=[taco.Asset(source)]))


def test_variable_leaf_requires_at_least_one_file() -> None:
    contract = taco.Contract(structure=["image*[1,3].tif"])
    with pytest.raises(SampleError, match="contiguous"):
        contract.validate_sample(taco.Sample(id="u5"))
    with pytest.raises(SampleError, match="contiguous"):
        contract.validate_sample(taco.Sample(id="u6", assets=[taco.Asset(b"x", path="image1.tif")]))


@pytest.mark.parametrize(
    "structure",
    [
        [],
        ["../a.bin"],
        ["a__b.bin"],
        ["bad[folder]/a.bin"],
        ["a.bin", "a.bin"],
        ["x*[2,1].bin"],
        ["x*[0,2].bin"],
        ["x*[0,0].bin"],
        ["x*[1,2].bin", "x0.bin"],
        ["x*[1,2]1", "x1*[1,2]"],
        ["x*[1,2]", "x0/a.bin"],
    ],
)
def test_invalid_structures(structure: list[str]) -> None:
    with pytest.raises(ContractError):
        taco.Contract(structure=structure)


def test_disjoint_variable_leaves_with_similar_names() -> None:
    taco.Contract(structure=["x*[1,2]1", "x1*[1,1]"])
    taco.Contract(structure=["x*[1,2]", "x9"])
    taco.Contract(structure=["x*[1,2]", "x9/a.bin"])


@pytest.mark.parametrize(
    ("structure", "message"),
    [
        (["Before/a.tif", "before/a.tif"], "'Before' and 'before' under the sample root differ only in case"),
        (["a.tif", "A.tif"], "'a.tif' and 'A.tif' under the sample root differ only in case"),
        (["img/a.tif", "IMG"], "'img' and 'IMG' under the sample root differ only in case"),
        (["IMG*[1,3].tif", "img*[1,3].tif"], "'IMG' and 'img' under the sample root differ only in case"),
        (["img*[1,3].tif", "IMG0.tif"], "'IMG0.tif' overlaps 'img\\*\\[1,3\\].tif'"),
        (["a*[1,20].tif", "A1*[1,3].tif"], "'a\\*\\[1,20\\].tif' overlaps 'A1\\*\\[1,3\\].tif'"),
    ],
)
def test_structure_names_must_differ_beyond_case(structure: list[str], message: str) -> None:
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=structure)


class CaseFields(BaseModel):
    split: str
    Split: str


def test_metadata_fields_must_differ_beyond_case() -> None:
    message = "metadata fields 'ml:split' and 'ml:Split' at sample differ only in case"
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", ml=CaseFields)])
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["a.bin"], metadata={"sample": {"ml:split": "string", "ml:Split": "string"}})


def test_profiles_require_canonical_nullability() -> None:
    fields = {
        "spatial:geometry": {"type": "binary", "nullable": True},
        "spatial:bbox": {"type": "list<double>", "nullable": False},
        "spatial:centroid": {"type": "struct<lon: float, lat: float>", "nullable": False},
        "spatial:proj_code": {"type": "string", "nullable": True},
        "spatial:proj_shape": {"type": "list<int64>", "nullable": True},
        "spatial:proj_transform": {"type": "list<double>", "nullable": True},
    }
    with pytest.raises(ContractError, match="canonical nullability"):
        taco.Contract(structure=["a.bin"], metadata={"sample": fields})


def test_passive_profile_validates_canonical_required_fields() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=[taco.Level("sample", stac=taco.metadata.sample.STAC)],
    )
    metadata = taco.Metadata(
        stac=taco.metadata.sample.STAC(
            proj_code="EPSG:4326",
            proj_shape=(16, 16),
            proj_transform=(1, 0, 0, 0, -1, 0),
            datetime=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
    )
    with pytest.raises(SampleError, match="stac:centroid"):
        contract.validate_sample(taco.Sample(id="missing-centroid", assets=b"x", metadata=metadata))


def test_scope_is_enforced() -> None:
    with pytest.raises(ContractError, match="cannot be used"):
        taco.Contract(
            structure=["a.tif"],
            metadata=[taco.Level("sample", scaling=taco.metadata.asset.Scaling)],
        )
    with pytest.raises(CollectionError, match="not collection"):
        taco.Collection(
            contract=taco.Contract(structure=["a.tif"]),
            id="scoped",
            description="Scoped metadata",
            licenses=["MIT"],
            providers=["me"],
            split=taco.metadata.sample.Split(split="train"),
        )
    with pytest.raises(ContractError, match="cannot be used"):
        taco.Contract(
            structure=["folder/a.tif"],
            metadata=[taco.Level("children", stac=taco.metadata.sample.STAC | None)],
        )
    taco.Contract(
        structure=["folder/a.tif"],
        metadata=[taco.Level("children", stac=taco.extensions.STAC(model=taco.metadata.folder.STAC))],
    )


def test_derived_declaration_and_dependency() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.STAC(), grid=taco.extensions.MajorTOM(50))],
    )
    descriptor = contract.extensions["sample"]["grid"]
    assert descriptor["requires"] == ["stac:centroid"]
    assert descriptor["produces"] == ["grid:code"]
    assert descriptor["configuration"]["dist_km"] == 50

    with pytest.raises(ContractError, match="missing"):
        taco.Contract(
            structure=["a.bin"],
            metadata=[taco.Level("sample", grid=taco.extensions.MajorTOM())],
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
            structure=["data.bin"],
            metadata=metadata,
            derived={
                "sample": {
                    "a": {"requires": ["missing:value"], "produces": ["a:value"]},
                }
            },
        )
    with pytest.raises(ContractError, match="cycle"):
        taco.Contract(
            structure=["data.bin"],
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
            structure=["data.bin"],
            metadata=metadata,
            derived={
                "sample": {
                    "a": {"requires": [], "produces": ["a:value", "a:value"]},
                }
            },
        )


def test_execution_graph_is_not_serialized() -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.STAC(), grid=taco.extensions.MajorTOM())],
    )
    assert contract.extensions
    assert "taco:derived" not in contract.to_dict()

    legacy = {**contract.to_dict(), "taco:derived": contract.extensions}
    loaded = taco.Contract.from_dict(legacy)
    assert loaded.extensions == contract.extensions
    assert "taco:derived" not in loaded.to_dict()


def test_derived_configuration_must_be_json() -> None:
    with pytest.raises(ContractError, match="JSON serializable"):
        taco.Contract(
            structure=["data.bin"],
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
        structure=["data.bin"],
        metadata=[taco.Level("sample", custom=Types)],
    )
    fields = contract.metadata["sample"]
    assert fields["custom:at"].type == "timestamp[ms, UTC]"
    assert fields["custom:values"].type == "list<int64>"
