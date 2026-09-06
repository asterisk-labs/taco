from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

import taco
from taco.contract import Curator, Extent, Provider
from taco.errors import CollectionError


def test_collection_round_trip(collection: taco.Collection) -> None:
    data = collection.to_dict()
    assert data["taco:version"] == "3.0.0"
    assert data["labels:num_classes"] == 2
    assert "metadata" not in data
    loaded = taco.Collection.from_json(collection.to_json())
    assert loaded.to_dict() == data


def test_collection_metadata_models() -> None:
    metadata = taco.CollectionMetadata(
        labels=taco.metadata.collection.Labels(classes=["cloud", "clear"]),
        optical=taco.metadata.collection.Optical(
            sensor="sentinel2msi",
            bands=[taco.metadata.collection.SpectralBand(name="B02", center_wavelength=490)],
        ),
        split=taco.metadata.collection.SplitStrategy(strategy="stratified"),
    )
    values = metadata.flatten()
    assert values["labels:num_classes"] == 2
    assert values["optical:num_bands"] == 1
    assert values["split:strategy"] == "stratified"


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


@pytest.mark.parametrize("version", ["1", "v1.0.0", "1.0", "01.0.0"])
def test_dataset_version_must_be_semver(collection: taco.Collection, version: str) -> None:
    with pytest.raises(CollectionError):
        collection.replace(dataset_version=version)


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


def test_collection_metadata_must_be_json(collection: taco.Collection) -> None:
    class Values(BaseModel):
        value: float

    with pytest.raises(CollectionError, match="JSON serializable"):
        collection.replace(metadata=taco.CollectionMetadata(values=Values(value=float("nan"))))
