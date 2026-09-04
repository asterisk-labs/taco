from __future__ import annotations

import json

import pytest

import taco
from taco.contract import Extent
from taco.errors import CollectionError, ContractError


def test_collection_round_trip(collection: taco.Collection) -> None:
    data = json.loads(collection.to_json())
    assert data["taco:structure"][0] == "before/B02.tif"
    assert data["providers"] == [{"name": "Asterisk Labs", "roles": ["producer"]}]
    assert data["labels:num_classes"] == 2
    clone = taco.Collection.from_dict(data)
    assert clone == collection
    assert clone.extra == {"labels:num_classes": 2}


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"id": ""}, "id is required"),
        ({"id": "a/b"}, "forbidden"),
        ({"dataset_version": "1.0"}, "SemVer"),
        ({"description": " "}, "description"),
        ({"licenses": []}, "licenses"),
        ({"providers": []}, "providers"),
        ({"tasks": ["ok", ""]}, "tasks"),
        ({"title": "x" * 251}, "250"),
        ({"extent": {"spatial": [1, 2, 3]}}, "west, south, east, north"),
        ({"extent": {"spatial": [0, 10, 0, -10]}}, "south"),
        ({"extent": {"spatial": [0, 0, 0, 0], "temporal": ["2024-02-01", "2024-01-01"]}}, "start must not exceed"),
        ({"extra": {"id": 1}}, "reserved"),
        ({"extra": {"taco:x": 1}}, "reserved"),
        ({"extra": {"blob": b"x"}}, "JSON"),
        ({"curators": [{"email": "nope"}]}, "name or an organization"),
        ({"providers": [{"name": "x", "url": "ftp://x"}]}, "http"),
    ],
)
def test_invalid_collections(collection: taco.Collection, changes, match) -> None:
    with pytest.raises(CollectionError, match=match):
        collection.replace(**changes)


def test_extent_normalizes_temporal() -> None:
    extent = Extent(spatial=[-10, -5, 10, 5], temporal=["2024-01-01T00:00:00+02:00", "2024-01-02"])
    assert extent.temporal == ("2023-12-31T22:00:00Z", "2024-01-02T00:00:00Z")
    crossing = Extent(spatial=[170, 0, -170, 10])
    assert crossing.crosses_antimeridian
    merged = Extent.union([extent, Extent(spatial=[20, 0, 30, 40], temporal=["2025-01-01", "2025-06-01"])])
    assert merged.spatial == (-10.0, -5.0, 30.0, 40.0)
    assert merged.temporal == ("2023-12-31T22:00:00Z", "2025-06-01T00:00:00Z")


def test_collection_from_dict_requires_contract() -> None:
    with pytest.raises((CollectionError, ContractError)):
        taco.Collection.from_dict(
            {
                "id": "x",
                "dataset_version": "1.0.0",
                "description": "d",
                "licenses": ["MIT"],
                "providers": ["p"],
                "tasks": ["t"],
            }
        )
