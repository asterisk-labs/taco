from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, ClassVar

import pyarrow as pa
import pytest
from pydantic import BaseModel, Field, create_model

import taco
from taco.errors import CollectionError, ContractError, SampleError

from . import models


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


def test_prepare_sample_flattens_metadata_once(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = taco.Contract(structure=["a.bin", "b.bin"], metadata=[taco.Level("sample", values=Values)])
    calls = []
    flatten = taco.Contract.flatten_metadata

    def counting(self, level, *args, **kwargs):
        calls.append(level)
        return flatten(self, level, *args, **kwargs)

    monkeypatch.setattr(taco.Contract, "flatten_metadata", counting)
    sample = taco.Sample(
        id="once",
        assets=[taco.Asset(b"x", path="a.bin"), taco.Asset(b"y", path="b.bin")],
        metadata=taco.Metadata(values=Values(count=1)),
    )
    prepared = contract.prepare_sample(sample)
    assert calls == ["sample", "children", "children"]
    assert prepared.metadata == {"values:count": 1, "values:score": None}


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
    message = "metadata fields 'info:split' and 'info:Split' at sample differ only in case"
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", info=CaseFields)])
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["a.bin"], metadata={"sample": {"info:split": "string", "info:Split": "string"}})


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


def test_a_profile_without_centroid_gets_one_computed() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.sample.stac.STAC)],
    )
    metadata = taco.Metadata(
        stac=taco.extensions.sample.stac.STAC(
            proj_code="EPSG:4326",
            proj_shape=(16, 16),
            proj_transform=(1, 0, 0, 0, -1, 0),
            datetime=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
    )
    contract.validate_sample(taco.Sample(id="missing-centroid", assets=b"x", metadata=metadata))
    assert contract.operations["sample"]["stac"]["produces"] == ["stac:centroid"]


def test_scope_is_enforced() -> None:
    with pytest.raises(ContractError, match="cannot be used"):
        taco.Contract(
            structure=["a.tif"],
            metadata=[taco.Level("sample", strategy=models.SplitStrategy)],
        )
    with pytest.raises(CollectionError, match="not collection"):
        taco.Collection(
            contract=taco.Contract(structure=["a.tif"]),
            id="scoped",
            description="Scoped metadata",
            licenses=["MIT"],
            providers=["me"],
            info=models.Split(split="train"),
        )
    taco.Contract(
        structure=["folder/a.tif"],
        metadata=[taco.Level("children", stac=taco.extensions.sample.stac.STAC | None)],
    )
    taco.Contract(
        structure=["folder/a.tif"],
        metadata=[taco.Level("children", stac=taco.extensions.sample.stac.STAC)],
    )


def test_derived_declaration_and_dependency() -> None:
    contract = taco.Contract(
        structure=["a.bin"],
        metadata=[
            taco.Level(
                "sample", stac=taco.extensions.sample.stac.STAC, majortom=taco.extensions.sample.majortom.MajorTOM(50)
            )
        ],
    )
    descriptor = contract.operations["sample"]["majortom"]
    assert descriptor["requires"] == ["stac:centroid"]
    assert descriptor["produces"] == ["majortom:code"]
    assert descriptor["configuration"]["dist_km"] == 50

    with pytest.raises(ContractError, match="missing"):
        taco.Contract(
            structure=["a.bin"],
            metadata=[taco.Level("sample", majortom=taco.extensions.sample.majortom.MajorTOM())],
        )


@pytest.mark.parametrize(
    ("namespace", "extension"),
    [
        ("grid", taco.extensions.sample.majortom.MajorTOM()),
        ("enrich", taco.extensions.sample.geoenrich.GeoEnrich(["elevation"])),
    ],
)
def test_extensions_keep_the_namespace_they_own(namespace: str, extension: taco.Extension) -> None:
    with pytest.raises(ContractError, match="must use metadata namespace"):
        taco.Level("sample", **{namespace: extension})


def test_execution_graph_is_not_serialized() -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[
            taco.Level(
                "sample", stac=taco.extensions.sample.stac.STAC, majortom=taco.extensions.sample.majortom.MajorTOM()
            )
        ],
    )
    assert contract.operations
    assert "taco:derived" not in contract.to_dict()

    assert taco.Contract.from_dict(contract.to_dict()).operations == {}


def test_serialized_extension_descriptors_are_rejected(collection: taco.Collection) -> None:
    legacy = {**collection.to_dict(), "taco:derived": {"sample": {}}}
    with pytest.raises(CollectionError, match="unknown reserved keys"):
        taco.Collection.from_dict(legacy)


def test_extension_configuration_must_be_json() -> None:
    @dataclass(frozen=True)
    class NotJSON(taco.Extension):
        @property
        def requires(self) -> tuple[str, ...]:
            return ()

        @property
        def fields(self) -> pa.Schema:
            return pa.schema([pa.field("value", pa.int64())])

        def configuration(self) -> dict[str, float]:
            return {"value": float("nan")}

        def run(self, context: taco.ExtensionContext) -> dict[str, list[int]]:
            return {"value": [1] * len(context.assets)}

    with pytest.raises(ContractError, match="JSON serializable"):
        taco.Contract(structure=["data.bin"], metadata=[taco.Level("sample", value=NotJSON())])


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


@pytest.mark.parametrize(
    ("structure", "message"),
    [
        (
            ["a_/b/x.tif", "a/_b/y.tif"],
            "levels 'children/a_/b' and 'children/a/_b' both map to METADATA/children__a___b.parquet$",
        ),
        (
            ["A_/b/x.tif", "a/_b/y.tif"],
            "levels 'children/A_/b' and 'children/a/_b' both map to METADATA/children__a___b.parquet "
            "on a case-insensitive file system",
        ),
    ],
)
def test_levels_need_distinct_metadata_files(structure: list[str], message: str) -> None:
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=structure)


def test_a_folder_name_may_end_with_an_underscore() -> None:
    contract = taco.Contract(structure=["a_/b/x.tif"])
    assert contract.levels == ("sample", "children", "children/a_", "children/a_/b")


@pytest.mark.parametrize(
    ("level", "group", "message"),
    [
        (
            "children",
            {"stac": taco.extensions.sample.stac.STAC, "majortom": taco.extensions.sample.majortom.MajorTOM()},
            "MajorTOM can only be used at metadata level 'sample', not 'children'",
        ),
        (
            "children",
            {"split": taco.extensions.sample.split.Split},
            "Split can only be used at metadata level 'sample', not 'children'",
        ),
        (
            "sample",
            {"split": taco.extensions.sample.split.Split | None},
            r"Split cannot be optional: \['split:split'\] must hold a value on every row at 'sample'",
        ),
    ],
)
def test_builtin_extensions_keep_their_level_and_nullability(level: str, group: dict, message: str) -> None:
    # Each one would write a dataset that its JSON Schema or validation rejects.
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["x/a.tif"], metadata=[taco.Level(level, **group)])


def test_an_optional_split_is_rejected_in_every_spelling() -> None:
    from typing import Optional

    # typing.Optional builds a typing.Union, not the types.UnionType of "Split | None".
    with pytest.raises(ContractError, match="Split cannot be optional"):
        taco.Level("sample", split=Optional[taco.extensions.sample.split.Split])  # noqa: UP045


def test_a_group_declares_its_levels_and_nullability() -> None:
    class Reading(BaseModel):
        __taco_levels__: ClassVar[frozenset[str]] = frozenset({"children"})
        __taco_nullable__: ClassVar[bool] = False

        value: int

    class Gauge(BaseModel):
        __taco_nullable__: ClassVar[bool] = False

        value: int | None

    assert taco.Level("children", reading=Reading).groups
    with pytest.raises(ContractError, match="Reading can only be used at metadata level 'children', not 'sample'"):
        taco.Level("sample", reading=Reading)
    with pytest.raises(ContractError, match="Reading cannot be optional"):
        taco.Level("children", reading=Reading | None)
    with pytest.raises(ContractError, match=r"Gauge columns cannot be nullable: \['gauge:value'\]"):
        taco.Level("sample", gauge=Gauge)
    assert taco.Level("sample", stac=taco.extensions.sample.stac.STAC | None).groups


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ("x*[01,3].bin", "malformed variable leaf"),
        ("x*[1,03].bin", "malformed variable leaf"),
        ("x*[1, 3].bin", "malformed variable leaf"),
        ("x*[ 1,3].bin", "malformed variable leaf"),
        ("x*[+1,3].bin", "malformed variable leaf"),
        ("x*[1,9007199254740992].bin", "has a bound above 9007199254740991"),
        ("x*[1,18446744073709551616].bin", "has a bound above 9007199254740991"),
        ("x*[1," + "9" * 5000 + "].bin", "has a bound above 9007199254740991"),
        ("x*[1,3].", "must not end with a space or dot"),
    ],
)
def test_sequence_bounds_are_plain_decimal_integers(declaration: str, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=[declaration])


@pytest.mark.parametrize("declaration", ["img.*[1,3].tif", "img *[1,3].tif", ".*[1,3].tif", "x*[1,9007199254740991]"])
def test_a_sequence_prefix_only_has_to_make_valid_names(declaration: str) -> None:
    contract = taco.Contract(structure=[declaration])
    name = declaration.split("*")[0] + "0" + declaration.split("]")[1]
    assert contract.validate_sample(taco.Sample(id="s", assets=[taco.Asset(b"x", path=name)])).assets


def test_a_dot_path_is_a_contract_error() -> None:
    with pytest.raises(ContractError, match=r"invalid structure path component '\.'"):
        taco.Contract(structure=["."])
    with pytest.raises(SampleError, match=r"invalid asset path component '\.'"):
        taco.Asset(b"x", path=".")
    with pytest.raises(SampleError, match=r"invalid folder path component '\.'"):
        taco.Folder(".", metadata=taco.Metadata())


@pytest.mark.parametrize("structure", [5, None, "a.bin"])
def test_structure_must_be_a_list_of_paths(structure: object) -> None:
    with pytest.raises(ContractError, match="non-empty list of paths"):
        taco.Contract(structure=structure)  # type: ignore[arg-type]


def test_a_metadata_field_name_must_be_a_string() -> None:
    with pytest.raises(ContractError, match="must be namespace:field"):
        taco.Contract(structure=["a.bin"], metadata={"sample": {5: "int64"}})  # type: ignore[dict-item]
    with pytest.raises(ContractError, match="unknown levels"):
        taco.Contract(structure=["a.bin"], metadata={5: {}, None: {}})  # type: ignore[dict-item]


def test_a_level_file_name_must_fit_the_file_system() -> None:
    # children__<folder>.parquet is 18 bytes longer than the folder name.
    assert taco.Contract(structure=["a" * 237 + "/x.tif"]).levels[-1] == "children/" + "a" * 237
    with pytest.raises(ContractError, match="a file name longer than 255 bytes"):
        taco.Contract(structure=["a" * 238 + "/x.tif"])
    with pytest.raises(ContractError, match=r"level 'children/a{90}/b{90}/c{90}' maps to METADATA/children__a{90}__"):
        taco.Contract(structure=[f"{'a' * 90}/{'b' * 90}/{'c' * 90}/x.tif"])


def _declared(type_: str = "string", *, nullable: bool = False) -> dict[str, object]:
    return {"type": type_, "nullable": nullable, "description": ""}


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {"children": {"split:split": _declared()}},
            "Split can only be used at metadata level 'sample', not 'children'",
        ),
        (
            {"sample": {"split:split": _declared(nullable=True)}},
            r"Split columns cannot be nullable: \['split:split'\] must hold a value on every row at 'sample'",
        ),
        (
            {"children/x": {"majortom:code": _declared()}},
            "MajorTOM can only be used at metadata level 'sample', not 'children/x'",
        ),
        (
            {"sample": {"majortom:code": _declared(nullable=True)}},
            r"MajorTOM columns cannot be nullable: \['majortom:code'\]",
        ),
    ],
)
def test_mapping_metadata_keeps_builtin_levels_and_nullability(metadata: dict, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["x/a.tif"], metadata=metadata)


def test_a_stored_contract_that_breaks_builtin_rules_still_reads() -> None:
    # validate reports these, so a reader can still open the dataset.
    contract = taco.Contract(structure=["x/a.tif"], metadata={"sample": {"split:split": _declared()}})
    data = contract.to_dict()
    data["taco:metadata"]["children"]["split:split"] = _declared(nullable=True)
    assert taco.Contract.from_dict(data).metadata["children"]["split:split"].nullable
    with pytest.raises(ContractError, match="Split can only be used"):
        taco.Contract(structure=data["taco:structure"], metadata=data["taco:metadata"])


class ColonOutput(taco.Extension):
    __taco_row_local__: ClassVar[bool] = True

    @property
    def requires(self) -> tuple[str, ...]:
        return ()

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("band:mean", pa.float64())])

    def run(self, context: taco.ExtensionContext) -> dict[str, list[float]]:
        return {"band:mean": [0.5] * len(context)}


@pytest.mark.parametrize(
    ("level", "message"),
    [
        (
            lambda: taco.Level("children", stats=ColonOutput()),
            "metadata field 'stats:band:mean' must be namespace:field",
        ),
        (
            lambda: taco.Level("sample", info=create_model("Empty", **{"": (int, ...)})),
            "metadata field 'info:' has no field name",
        ),
        (
            lambda: taco.Level("sample", info=create_model("Colon", **{"a:b": (int, ...)})),
            "metadata field 'info:a:b' must be namespace:field",
        ),
        (
            lambda: taco.Level(
                "sample", info=create_model("Spaced", value=(Annotated[dict, pa.struct([(" a", pa.int64())])], ...))
            ),
            "metadata field 'info:value': struct field name ' a'",
        ),
        (
            lambda: taco.Level(
                "sample", info=create_model("Colons", value=(Annotated[dict, pa.struct([("a:b", pa.int64())])], ...))
            ),
            "metadata field 'info:value': struct field name 'a:b'",
        ),
        (
            lambda: taco.Level(
                "sample", info=create_model("Nested", value=(create_model("Inner", **{"a,b": (int, ...)}), ...))
            ),
            "metadata field 'info:value': struct field name 'a,b'",
        ),
        (
            lambda: taco.Level("sample", info=create_model("Hollow", value=(create_model("Inner"), ...))),
            "metadata field 'info:value': struct needs at least one field",
        ),
        (
            lambda: taco.Level("sample", info=create_model("View", value=(Annotated[str, pa.string_view()], ...))),
            "metadata field 'info:value': unsupported type 'string_view'",
        ),
        (
            lambda: taco.Level(
                "sample", info=create_model("Scaled", value=(Annotated[Decimal, pa.decimal128(5, 10)], ...))
            ),
            "metadata field 'info:value': decimal128 scale must be between 0 and its precision",
        ),
        (
            lambda: taco.Level(
                "sample", info=create_model("Narrow", value=(Annotated[Decimal, pa.decimal32(5, 2)], ...))
            ),
            r"metadata field 'info:value': decimal32\(5, 2\) has no canonical type string",
        ),
    ],
)
def test_levels_refuse_what_the_stored_contract_cannot_express(level: Callable[[], taco.Level], message: str) -> None:
    # Each of these would write a COLLECTION.json that open_dataset and validate reject.
    with pytest.raises(ContractError, match=message):
        level()


def test_level_types_read_back_from_the_stored_contract() -> None:
    class Inner(BaseModel):
        name: str
        score: float | None

    class Rich(BaseModel):
        nested: Inner
        items: list[Inner | None]
        mapped: dict[str, list[int]]
        spaced: Annotated[dict, pa.struct([("my field", pa.int8())])]
        stamp: Annotated[datetime, pa.timestamp("ms", tz="+05:30")]
        exact: Annotated[Decimal, pa.decimal256(40, 40)]
        raw: Annotated[bytes, pa.binary(4)]

    contract = taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", rich=Rich, values=Values)])
    loaded = taco.Contract.from_dict(contract.to_dict())
    assert loaded == contract
    assert loaded.arrow_types("sample") == contract.arrow_types("sample")
