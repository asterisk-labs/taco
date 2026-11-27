from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from random import Random
from typing import Any

import pytest
from pydantic import BaseModel, RootModel

import taco
from taco.contract import Curator, Extent, Provider
from taco.errors import CollectionError, TacoError

from . import models


def test_collection_round_trip(collection: taco.Collection) -> None:
    data = collection.to_dict()
    assert data["taco:version"] == "3.0.0"
    assert data["legend:num_classes"] == 2
    assert data["majortom:dist_km"] == 100
    assert data["majortom:centroid"] == "stac:centroid"
    assert "metadata" not in data
    assert "taco:derived" not in data
    loaded = taco.Collection.from_json(collection.to_json())
    assert loaded.to_dict() == data


def test_collection_metadata_models(collection: taco.Collection) -> None:
    collection = collection.replace(
        legend=models.Legend(classes=["cloud", "clear"]),
        optical=models.Optical(
            sensor="sentinel2msi",
            bands=[models.SpectralBand(name="B02", center_wavelength=490)],
        ),
        strategy=models.SplitStrategy(strategy="stratified"),
    )
    values = collection.to_dict()
    assert values["legend:num_classes"] == 2
    assert values["optical:num_bands"] == 1
    assert values["strategy:strategy"] == "stratified"
    assert collection.metadata["strategy"] == {"strategy": "stratified"}


class POI(BaseModel):
    category: str


def test_collection_metadata_groups_are_keywords(collection: taco.Collection) -> None:
    grouped = collection.replace(poi={"category": "volcano", "tags": ("a", "b")}, source={"collection": "s2"})
    assert grouped.metadata["poi"] == {"category": "volcano", "tags": ["a", "b"]}
    assert grouped.metadata["legend"]["num_classes"] == 2
    data = grouped.to_dict()
    assert data["poi:category"] == "volcano"
    assert data["source:collection"] == "s2"
    assert "metadata" not in data
    loaded = taco.Collection.from_json(grouped.to_json())
    assert loaded.to_dict() == data
    # Read back, the values MajorTOM stored are one more group.
    assert loaded.metadata["majortom"]["dist_km"] == 100
    assert set(loaded.replace(poi=None).metadata) == {"legend", "majortom", "source"}
    assert taco.Collection(**_required(collection), poi=POI(category="volcano"), empty=None).metadata == {
        "poi": {"category": "volcano"}
    }


def _required(collection: taco.Collection) -> dict[str, object]:
    return {
        "contract": collection.contract,
        "id": collection.id,
        "description": collection.description,
        "licenses": collection.licenses,
        "providers": collection.providers,
    }


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ({"metadata": {"poi": {"category": "x"}}}, "instead of metadata="),
        ({"licences": ["MIT"]}, "got list; did you mean 'licenses'"),
        ({"titel": "Tiny"}, "got str; did you mean 'title'"),
        ({"poi": POI}, r"needs an instance, such as POI\(\.\.\.\)"),
        ({"poi": {}}, "is empty"),
        ({"poi": {"a:b": 1}}, "must be namespace:field"),
        ({"poi": {"a__b": 1}}, "must not contain '__'"),
        ({"poi": {1: "x"}}, "non-string field"),
        ({"Poi": {"a": 1}}, "invalid metadata namespace"),
        ({"taco": {"a": 1}}, "reserved"),
        ({"poi": {"a": float("nan")}}, "JSON serializable"),
        ({"majortom": {"dist_km": 50}}, "conflicts with the active extension"),
    ],
)
def test_collection_metadata_groups_are_checked(
    collection: taco.Collection, groups: dict[str, object], message: str
) -> None:
    with pytest.raises(CollectionError, match=message):
        taco.Collection(**_required(collection), **groups)


def test_collection_parameters_are_keyword_only(collection: taco.Collection) -> None:
    with pytest.raises(TypeError, match="positional"):
        taco.Collection(collection.contract, "positional", "d", ["MIT"], ["me"])  # type: ignore[misc]
    with pytest.raises(CollectionError, match="instead of metadata="):
        collection.replace(metadata={"poi": {"category": "x"}})


def test_collection_reads_a_group_named_like_a_parameter(collection: taco.Collection) -> None:
    data = collection.to_dict()
    data["title:note"] = "kept"
    loaded = taco.Collection.from_dict(data)
    assert loaded.metadata["title"] == {"note": "kept"}
    assert loaded.title == collection.title
    assert loaded.to_dict() == data


def test_provider_and_curator() -> None:
    provider = Provider.from_any({"name": "Asterisk", "roles": ["producer"], "url": "https://asterisk.coop"})
    curator = Curator.from_any({"name": "Cesar Aybar", "email": "cesar@example.com"})
    assert provider.to_dict()["name"] == "Asterisk"
    assert curator.to_dict()["email"] == "cesar@example.com"
    with pytest.raises(CollectionError):
        Provider("")
    with pytest.raises(CollectionError):
        Curator()


def test_extent() -> None:
    extent = Extent(
        spatial=(-80, -20, -70, 0),
        temporal=("2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z"),
    )
    assert extent.to_dict()["spatial"] == [-80.0, -20.0, -70.0, 0.0]
    merged = Extent.union([extent, Extent((-75, -30, -60, 5))])
    assert merged is not None
    assert merged.spatial == (-80.0, -30.0, -60.0, 5.0)


def test_tasks_are_optional(collection: taco.Collection) -> None:
    untasked = collection.replace(tasks=None)
    data = untasked.to_dict()
    assert "tasks" not in data
    assert taco.Collection.from_dict(json.loads(json.dumps(data))).tasks is None


def test_reader_rejects_wrong_spec_version(collection: taco.Collection) -> None:
    data = collection.to_dict()
    data["taco:version"] = "2.0.0"
    with pytest.raises(CollectionError, match=r"3\.0\.0"):
        taco.Collection.from_dict(data)


def test_unknown_collection_fields_need_namespace(collection: taco.Collection) -> None:
    data = collection.to_dict()
    data["unknown"] = 1
    with pytest.raises(CollectionError, match="unqualified"):
        taco.Collection.from_dict(data)
    data.pop("unknown")
    data["custom:value"] = {"valid": True}
    assert taco.Collection.from_dict(json.loads(json.dumps(data))).to_dict()["custom:value"] == {"valid": True}


def test_unknown_reserved_collection_fields_are_rejected(collection: taco.Collection) -> None:
    data = collection.to_dict()
    data["taco:future"] = True
    with pytest.raises(CollectionError, match="reserved"):
        taco.Collection.from_dict(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "bad/id"),
        ("description", ""),
        ("licenses", []),
        ("providers", []),
        ("tasks", []),
        ("metadata", {}),
    ],
)
def test_collection_fields_are_checked(collection: taco.Collection, field: str, value: object) -> None:
    with pytest.raises(CollectionError):
        collection.replace(**{field: value})


@pytest.mark.parametrize(
    "value",
    [
        {"spatial": [0, 0, 1]},
        {"spatial": [0, 10, 1, -10]},
        {"spatial": [0, 0, 200, 1]},
        {"spatial": [0, 0, 1, 1], "temporal": ["2025-01-01", "2024-01-01"]},
        # In UTC these fall outside the years Python can hold.
        {"spatial": [0, 0, 1, 1], "temporal": ["0001-01-01T00:00:00+01:00", "2024-01-01"]},
        {"spatial": [0, 0, 1, 1], "temporal": ["2024-01-01", "9999-12-31T23:59:59-01:00"]},
    ],
)
def test_invalid_extent(value: object) -> None:
    with pytest.raises(CollectionError):
        Extent.from_any(value)


def test_antimeridian_extent_union() -> None:
    first = Extent((170, -10, -170, 10))
    second = Extent((175, -20, -175, 20))
    merged = Extent.union([first, second])
    assert first.crosses_antimeridian
    assert merged is not None
    assert merged.crosses_antimeridian


def test_point_extent_union() -> None:
    extent = Extent((-63.42020466006154, -12, -63.42020466006154, -12))

    assert Extent.union([extent]) == extent


@pytest.mark.parametrize(
    "spatial",
    [(0.1, 0.0, 0.2, 1.0), (-73.3, -12.0, -73.1, -11.9), (10.7, 45.1, 11.3, 45.9), (170.3, -1.0, -170.1, 1.0)],
)
def test_union_of_one_extent_is_that_extent(spatial: tuple[float, float, float, float]) -> None:
    extent = Extent(spatial, ("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"))
    assert Extent.union([extent]) == extent


def test_extent_union_tie_does_not_cross_the_antimeridian() -> None:
    # Either cover leaves out a 20 degree gap.
    merged = Extent.union([Extent((-170, 0, -10, 1)), Extent((10, 0, 170, 1))])
    assert merged == Extent((-170, 0, 170, 1))


def test_extent_union_matches_the_writer_summary() -> None:
    from taco.extensions._builtin.stac.models import _SpatialExtent

    random = Random(7)
    for _ in range(500):
        # Coarse coordinates make equal gaps, and so ties, common.
        boxes = []
        for _ in range(random.randint(1, 5)):
            south = random.randint(-18, 17) * 5
            boxes.append((random.randint(-36, 36) * 5, south, random.randint(-36, 36) * 5, south + 5))
        summary = _SpatialExtent()
        empty = [None] * len(boxes)
        summary.update(
            {"geometry": empty, "bbox": boxes, "proj_code": empty, "proj_shape": empty, "proj_transform": empty}
        )
        assert Extent.union([Extent(box) for box in boxes]) == Extent.from_any(summary.finish()), boxes


def test_bad_provider_and_curator_fields() -> None:
    with pytest.raises(CollectionError, match="url"):
        Provider("x", url="example.com")
    with pytest.raises(CollectionError, match="links"):
        Provider("x", links=("bad",))
    with pytest.raises(CollectionError, match="email"):
        Curator(name="x", email="bad")
    with pytest.raises(CollectionError, match="unknown"):
        Provider.from_any({"name": "x", "extra": True})
    with pytest.raises(CollectionError, match="unknown"):
        Curator.from_any({"name": "x", "extra": True})


def test_serialized_people_are_objects(collection: taco.Collection) -> None:
    data = collection.to_dict()
    data["providers"] = ["me"]
    with pytest.raises(CollectionError, match="list of objects"):
        taco.Collection.from_dict(data)

    data = collection.to_dict()
    data["curators"] = ["me"]
    with pytest.raises(CollectionError, match="list of objects"):
        taco.Collection.from_dict(data)


def test_collection_json_must_be_json() -> None:
    with pytest.raises(CollectionError, match="valid JSON"):
        taco.Collection.from_json("{")
    with pytest.raises(CollectionError, match="must be an object"):
        taco.Collection.from_json("[]")


def _with_raw(collection: taco.Collection, edit: Callable[[dict[str, Any]], object], raw: str) -> str:
    """COLLECTION.json text where the value "<raw>" is replaced by the JSON text raw."""
    data = collection.to_dict()
    edit(data)
    return json.dumps(data).replace('"<raw>"', raw)


@pytest.mark.parametrize(
    ("edit", "raw"),
    [
        pytest.param(lambda data: data.update(title="<raw>"), "NaN", id="nan"),
        pytest.param(lambda data: data.update(title="<raw>"), "Infinity", id="infinity"),
        pytest.param(lambda data: data["providers"][0].update(links=[{"href": "<raw>"}]), "-Infinity", id="links"),
        pytest.param(lambda data: data.update({"poi:values": "<raw>"}), "[1, NaN]", id="group"),
        pytest.param(lambda data: data.update(title="<raw>"), '"\\ud800"', id="lone-surrogate"),
        pytest.param(lambda data: data.update({"poi:nested": "<raw>"}), "[" * 257 + "]" * 257, id="too-deep"),
        pytest.param(lambda data: data.update({"poi:size": "<raw>"}), "1e400", id="float-overflow"),
        pytest.param(lambda data: data.update({"poi:size": "<raw>"}), "-1" + "0" * 400, id="integer-overflow"),
        pytest.param(lambda data: data.update({"poi:size": "<raw>"}), '1, "poi:size": 2', id="repeated-key"),
        pytest.param(lambda data: data.update(title="<raw>"), '"a", "title": "a"', id="repeated-equal-key"),
        pytest.param(
            lambda data: data["providers"][0].update(name="<raw>"), '"p", "name": "q"', id="repeated-nested-key"
        ),
    ],
)
def test_collection_json_is_as_strict_as_the_native_reader(
    collection: taco.Collection, edit: Callable[[dict[str, Any]], object], raw: str
) -> None:
    with pytest.raises(CollectionError, match="not valid JSON"):
        taco.Collection.from_json(_with_raw(collection, edit, raw))


def test_collection_json_may_nest_as_deep_as_the_native_reader_allows(collection: taco.Collection) -> None:
    text = _with_raw(collection, lambda data: data.update({"poi:nested": "<raw>"}), "[" * 256 + "]" * 256)
    assert taco.Collection.from_json(text).to_json()


def test_collection_json_numbers_may_reach_the_largest_double(collection: taco.Collection) -> None:
    raw = "[1.7976931348623157e308, -1.7976931348623157e+308, 1e-400, 1" + "0" * 300 + "]"
    loaded = taco.Collection.from_json(_with_raw(collection, lambda data: data.update({"poi:sizes": "<raw>"}), raw))
    assert loaded.metadata["poi"]["sizes"] == [1.7976931348623157e308, -1.7976931348623157e308, 0.0, 10**300]


def test_validate_reports_a_repeated_key(folder_dataset: Path) -> None:
    path = folder_dataset / "COLLECTION.json"
    text = path.read_text()
    path.write_text(text.replace('"id": ', '"id": "again", "id": ', 1))
    report = taco.validate(folder_dataset)
    assert [issue.code for issue in report.errors] == ["container"]
    assert "repeats the key 'id'" in report.errors[0].message


@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(lambda text: b"\xef\xbb\xbf" + text.encode(), id="bom"),
        pytest.param(lambda text: text.encode("utf-16"), id="utf-16"),
        pytest.param(lambda text: text.encode().replace(b"Tiny", b"\xe9"), id="latin-1"),
    ],
)
def test_collection_json_bytes_must_be_utf8(collection: taco.Collection, encode: Callable[[str], bytes]) -> None:
    with pytest.raises(CollectionError, match="not valid JSON"):
        taco.Collection.from_json(encode(collection.to_json()))


def test_parsed_collection_values_must_be_json(collection: taco.Collection) -> None:
    data = collection.to_dict()
    data["providers"][0]["links"] = [{"href": math.nan}]
    with pytest.raises(CollectionError, match="not valid JSON"):
        taco.Collection.from_dict(data)
    with pytest.raises(CollectionError, match="links must be JSON serializable"):
        Provider("x", links=({"href": math.inf},))


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(lambda data: data.update(title=123), "title must be a string", id="title"),
        pytest.param(lambda data: data["providers"][0].update(url=5), "provider url", id="provider-url"),
        pytest.param(lambda data: data["providers"][0].update(links=["x"]), "provider links", id="provider-links"),
        pytest.param(lambda data: data.update(curators=[{"name": 7}]), "curator name must be", id="curator-name"),
        pytest.param(lambda data: data.update(curators=[{"name": "c", "email": 5}]), "curator email", id="email"),
        pytest.param(lambda data: data.update(curators=[{"name": "c", "role": ["x"]}]), "curator role", id="role"),
        pytest.param(lambda data: data.update({"taco:structure": {"mask.tif": None}}), "structure", id="structure"),
        pytest.param(lambda data: data.update({"taco:sources": ["a.zip"]}), "taco:sources", id="sources"),
        pytest.param(lambda data: data.update(extent={"spatial": ["0", "0", "1", "1"]}), "numbers", id="strings"),
        pytest.param(lambda data: data.update(extent={"spatial": [False, 0, True, 1]}), "numbers", id="booleans"),
        pytest.param(
            lambda data: data.update(extent={"spatial": [0, 0, 1, 1], "temporal": 5}), "start, end", id="temporal"
        ),
        pytest.param(
            lambda data: data.update(
                extent={"spatial": [0, 0, 1, 1], "temporal": {"a": "2024-01-01", "b": "2024-01-02"}}
            ),
            "start, end",
            id="temporal-object",
        ),
    ],
)
def test_collection_json_values_have_their_spec_types(
    collection: taco.Collection, edit: Callable[[dict[str, Any]], object], message: str
) -> None:
    data = collection.to_dict()
    edit(data)
    with pytest.raises(TacoError, match=message):
        taco.Collection.from_dict(data)


def test_collection_metadata_must_be_json(collection: taco.Collection) -> None:
    class Values(BaseModel):
        value: float

    with pytest.raises(CollectionError, match="JSON serializable"):
        collection.replace(values=Values(value=float("nan")))
    with pytest.raises(CollectionError, match="JSON serializable"):
        collection.replace(values={"value": float("inf")})


def test_serialized_metadata_does_not_share_nested_values(collection: taco.Collection) -> None:
    grouped = collection.replace(poi={"tags": ["volcano"], "location": {"country": "Peru"}})
    data = grouped.to_dict()
    data["poi:tags"].append("changed")
    data["poi:location"]["country"] = "changed"
    assert grouped.metadata["poi"] == {"tags": ["volcano"], "location": {"country": "Peru"}}


@pytest.mark.parametrize("root", ["volcano", ["volcano"], 42, None])
def test_collection_model_must_serialize_to_an_object(collection: taco.Collection, root: object) -> None:
    with pytest.raises(CollectionError, match="must serialize to a JSON object"):
        collection.replace(poi=RootModel[object](root))


def test_collection_accepts_a_root_mapping(collection: taco.Collection) -> None:
    grouped = collection.replace(poi=RootModel[dict[str, str]]({"category": "volcano"}))
    assert grouped.metadata["poi"] == {"category": "volcano"}


def test_collection_wraps_model_serialization_errors(collection: taco.Collection) -> None:
    class Values(BaseModel):
        value: object

    with pytest.raises(CollectionError, match=r"group 'values'.*JSON serializable"):
        collection.replace(values=Values(value=object()))
