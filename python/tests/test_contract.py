from __future__ import annotations

from pathlib import Path

import pytest

import taco
from taco.errors import ContractError, SampleError


def test_levels_follow_declaration_order(contract: taco.Contract) -> None:
    assert contract.levels == ("collection", "sample", "sample/before", "sample/after")
    assert contract.folders == {("before",), ("after",)}
    assert contract.is_folder((), "before")
    assert not contract.is_folder((), "mask.tif")


def test_nested_levels_sorted_by_depth_then_declaration() -> None:
    contract = taco.Contract(structure=["x/y/z.tif", "a/b.tif", "x/w.tif"])
    assert contract.levels == ("collection", "sample", "sample/x", "sample/a", "sample/x/y")
    assert contract.metadata["sample/x/y"] == {}


def test_null_structure_has_only_collection_level() -> None:
    contract = taco.Contract(structure=None, metadata={"collection": {"label": "int8"}})
    assert contract.is_null
    assert contract.levels == ("collection",)
    assert contract.to_dict()["taco:structure"] is None
    assert contract.metadata["collection"]["label"] == ("int8", "")


def test_metadata_types_are_canonicalized() -> None:
    contract = taco.Contract(
        structure=["a.tif"],
        metadata={"collection": {"x": ["float64", "d"], "t": ("timestamp[us, tz=UTC]", "when")}},
    )
    assert contract.metadata["collection"]["x"] == ("double", "d")
    assert contract.metadata["collection"]["t"] == ("timestamp[us, UTC]", "when")


def test_round_trip_and_equality(contract: taco.Contract) -> None:
    clone = taco.Contract.from_dict(contract.to_dict())
    assert clone == contract
    assert hash(clone) == hash(contract)
    assert clone.to_dict() == contract.to_dict()
    assert "sample__before" not in str(contract.describe())
    assert "sample/before" in contract.describe()


@pytest.mark.parametrize(
    ("structure", "metadata", "match"),
    [
        ([], None, "at least one leaf"),
        (["a.tif", "a.tif"], None, "twice"),
        (["img*[3,2].tif"], None, "min > max"),
        (["img*[0,0].tif"], None, "never"),
        (["img[1].tif"], None, "malformed"),
        (["a/b.tif", "a"], None, "sibling identifier"),
        (["img0.tif", "img*[1,2].tif"], None, "matches variable leaf"),
        (["img*[1,2].tif", "img1*[0,1].tif"], None, "overlap"),
        (["bad:name.tif"], None, "forbidden characters"),
        (["a__b.tif"], None, "'__'"),
        (["../x.tif"], None, "invalid"),
        (["/abs.tif"], None, "relative"),
        (["niño.tif"], None, "ASCII"),
        (["a.tif"], {"collection": {"internal:x": "int32"}}, "reserved"),
        (["a.tif"], {"collection": {"x__y": "int32"}}, "'__'"),
        (["a.tif"], {"nope": {}}, "do not exist"),
        (["a.tif"], {"collection": {"x": ["int32"]}}, "\\[type, description\\]"),
        (["a.tif"], {"collection": {"x": ["nope", "d"]}}, "unsupported type"),
    ],
)
def test_invalid_contracts(structure, metadata, match) -> None:
    with pytest.raises(ContractError, match=match):
        taco.Contract(structure=structure, metadata=metadata)


def test_variable_leaf_matching() -> None:
    contract = taco.Contract(structure=["img*[2,4].tif"])
    leaf = contract.leaves[0]
    assert leaf.variable and leaf.identifier == "img"
    assert leaf.match_index("img0.tif") == 0
    assert leaf.match_index("img12.tif") == 12
    assert leaf.match_index("img01.tif") is None
    assert leaf.match_index("img.tif") is None
    assert leaf.match_index("imgx.tif") is None
    assert leaf.instance_name(3) == "img3.tif"


def _sources(tmp_path: Path, names: list[str]) -> dict[str, Path]:
    result = {}
    for name in names:
        file = tmp_path / name.replace("/", "_")
        file.write_bytes(b"x")
        result[name] = file
    return result


def test_validate_sample_orders_assets_and_fills_empty_levels(tmp_path: Path) -> None:
    contract = taco.Contract(structure=["b.tif", "a.tif", "sub/c.tif"], metadata={"collection": {"n": "int32"}})
    sample = taco.Sample(
        assets=_sources(tmp_path, ["sub/c.tif", "a.tif", "b.tif"]),
        metadata={"collection": {"n": 1}},
    )
    validated = contract.validate_sample(sample)
    assert [asset.path for asset in validated.assets] == ["b.tif", "a.tif", "sub/c.tif"]
    assert validated.metadata == {
        "collection": {"n": 1},
        "sample": {"b.tif": {}, "a.tif": {}, "sub": {}},
        "sample/sub": {"c.tif": {}},
    }


def test_validate_sample_variable_leaves(tmp_path: Path) -> None:
    contract = taco.Contract(structure=["img*[2,4].tif"], metadata={"sample": {"q": "double"}})
    assets = _sources(tmp_path, ["img2.tif", "img0.tif", "img1.tif"])
    sample = taco.Sample(assets=assets, metadata={"sample": {f"img{i}.tif": {"q": i / 2} for i in range(3)}})
    validated = contract.validate_sample(sample)
    assert [asset.path for asset in validated.assets] == ["img0.tif", "img1.tif", "img2.tif"]

    with pytest.raises(SampleError, match="contiguous"):
        contract.validate_sample(
            taco.Sample(
                assets=_sources(tmp_path, ["img0.tif", "img2.tif"]),
                metadata={"sample": {"img0.tif": {"q": 1}, "img2.tif": {"q": 2}}},
            )
        )
    with pytest.raises(SampleError, match="contiguous"):
        contract.validate_sample(
            taco.Sample(assets=_sources(tmp_path, ["img0.tif"]), metadata={"sample": {"img0.tif": {"q": 1}}})
        )


def test_validate_sample_errors(contract: taco.Contract, make_sample) -> None:
    good = make_sample(0)
    contract.validate_sample(good)

    missing = taco.Sample(assets=good.assets[1:], metadata=good.metadata)
    with pytest.raises(SampleError, match="missing"):
        contract.validate_sample(missing)

    extra_asset = taco.Sample(
        assets=[*good.assets, taco.Asset("other.tif", good.assets[0].source)], metadata=good.metadata
    )
    with pytest.raises(SampleError, match="do not match"):
        contract.validate_sample(extra_asset)

    bad_child = taco.Sample(
        assets=good.assets, metadata={**good.metadata, "sample/before": {"B02.tif": {"resolution": 10}}}
    )
    with pytest.raises(SampleError, match="metadata children"):
        contract.validate_sample(bad_child)

    bad_field = taco.Sample(
        assets=good.assets,
        metadata={**good.metadata, "collection": {**good.metadata["collection"], "cloud_cover": "high"}},
    )
    with pytest.raises(SampleError, match="cloud_cover"):
        contract.validate_sample(bad_field)

    unknown_level = taco.Sample(assets=good.assets, metadata={**good.metadata, "sample/nope": {}})
    with pytest.raises(SampleError, match="unknown levels"):
        contract.validate_sample(unknown_level)

    missing_field = dict(good.metadata["collection"])
    missing_field.pop("split")
    with pytest.raises(SampleError, match="missing=\\['split'\\]"):
        contract.validate_sample(
            taco.Sample(assets=good.assets, metadata={**good.metadata, "collection": missing_field})
        )


def test_null_structure_sample(tmp_path: Path) -> None:
    contract = taco.Contract(structure=None)
    file = tmp_path / "x.bin"
    file.write_bytes(b"1")
    validated = contract.validate_sample(taco.Sample(assets=file))
    assert validated.single and validated.assets[0].path is None
    with pytest.raises(SampleError, match="null"):
        contract.validate_sample(taco.Sample(assets={"a.tif": file}))


def test_asset_normalization(tmp_path: Path) -> None:
    asset = taco.Asset("a/b.tif", b"bytes")
    assert asset.is_inline and asset.size() == 5
    with pytest.raises(SampleError):
        taco.Asset("../escape.tif", b"x")
    with pytest.raises(SampleError):
        taco.Asset("a.tif", 42)
    sample = taco.Sample(assets=[("a.tif", b"x"), taco.Asset("b.tif", b"y")])
    assert [item.path for item in sample.assets] == ["a.tif", "b.tif"]
    with pytest.raises(SampleError):
        taco.Sample(assets=[])
