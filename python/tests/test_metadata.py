from __future__ import annotations

import struct
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Annotated, Literal

import pyarrow as pa
import pytest
from pydantic import BaseModel, computed_field

import taco
from taco.errors import ContractError, SampleError


def point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def test_sample_metadata_facade_keeps_public_imports() -> None:
    assert taco.metadata.sample.STAC is taco.metadata.spatiotemporal.STAC
    assert taco.metadata.sample.ISTAC is taco.metadata.spatiotemporal.ISTAC
    assert taco.metadata.sample.MajorTOM is taco.metadata.derived.MajorTOM
    assert taco.metadata.sample.Split is taco.metadata.split.Split


def test_builtin_models() -> None:
    raster = taco.metadata.asset.Raster(resolution=10, num_bands=13, data_type="uint16")
    scaling = taco.metadata.asset.Scaling(scale_factor=0.01, scale_offset=[1], padding=[1, 2, 3, 4])
    stats = taco.metadata.asset.RasterStats(stats=[[0, 1, 0.5]])
    split = taco.metadata.sample.Split(split="train")
    assert raster.num_bands == 13
    assert scaling.scale_factor == [0.01]
    assert stats.stats == [[0.0, 1.0, 0.5]]
    assert split.split == "train"


def test_scaling_validation() -> None:
    with pytest.raises(ValueError, match="zero"):
        taco.metadata.asset.Scaling(scale_factor=[1, 0])
    with pytest.raises(ValueError, match="top"):
        taco.metadata.asset.Scaling(padding=[1, 2])


def test_stac_time_order() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="time_start"):
        taco.metadata.sample.STAC(
            crs="EPSG:4326",
            tensor_shape=(3, 256, 256),
            geotransform=(-0.5, 1 / 256, 0, 0.5, 0, -1 / 256),
            centroid=point(0, 0),
            time_start=now,
            time_end=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )


def test_stac_and_istac_have_distinct_v2_profiles() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 3, tzinfo=timezone.utc)
    stac = taco.metadata.sample.STAC(
        crs="EPSG:4326",
        tensor_shape=(13, 256, 256),
        geotransform=(-76.1, 0.2 / 256, 0, -11.9, 0, -0.2 / 256),
        time_start=now,
        centroid=point(-76, -12),
        time_end=end,
    )
    istac = taco.metadata.sample.ISTAC(
        crs="EPSG:4326",
        geometry=point(-76, -12),
        time_start=now,
        time_end=end,
        centroid=point(-76, -12),
    )

    assert tuple(type(stac).model_fields) == (
        "crs",
        "tensor_shape",
        "geotransform",
        "time_start",
        "centroid",
        "time_end",
        "time_middle",
    )
    assert tuple(type(istac).model_fields) == (
        "crs",
        "geometry",
        "time_start",
        "time_end",
        "time_middle",
        "centroid",
    )
    assert stac.time_middle == istac.time_middle == datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert not issubclass(taco.metadata.sample.ISTAC, taco.metadata.sample.STAC)


def test_stac_grid_validation() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    common = {"crs": "EPSG:4326", "time_start": now, "centroid": point(0, 0)}
    with pytest.raises(ValueError, match="at least 2"):
        taco.metadata.sample.STAC(tensor_shape=(256,), geotransform=(0, 1, 0, 0, 0, -1), **common)
    with pytest.raises(ValueError, match="positive"):
        taco.metadata.sample.STAC(tensor_shape=(0, 256), geotransform=(0, 1, 0, 0, 0, -1), **common)
    with pytest.raises(ValueError, match="Field required"):
        taco.metadata.sample.STAC(tensor_shape=(256, 256), geotransform=(0, 1, 0), **common)


def test_spatial_models_require_canonical_namespaces() -> None:
    with pytest.raises(ContractError, match="must use metadata namespace 'stac'"):
        taco.Level("sample", location=taco.metadata.sample.STAC)
    with pytest.raises(ContractError, match="must use metadata namespace 'istac'"):
        taco.Level("sample", stac=taco.metadata.sample.ISTAC)


def test_contract_rejects_stac_and_istac_on_same_level() -> None:
    with pytest.raises(ContractError, match="either STAC or ISTAC"):
        taco.Contract(
            structure=None,
            metadata=taco.MetadataSchema(
                taco.Level("sample", stac=taco.metadata.sample.STAC, istac=taco.metadata.sample.ISTAC)
            ),
        )

    with pytest.raises(ContractError, match="puts geometry in STAC"):
        taco.Contract(
            structure=None,
            metadata={
                "sample": {
                    "stac:geometry": "binary",
                    "stac:centroid": "binary",
                }
            },
        )

    with pytest.raises(ContractError, match="puts affine-grid fields in ISTAC"):
        taco.Contract(
            structure=None,
            metadata={
                "sample": {
                    "istac:geometry": "binary",
                    "istac:tensor_shape": "list<int64>",
                }
            },
        )

    with pytest.raises(ContractError, match=r"STAC metadata.*missing fields"):
        taco.Contract(structure=None, metadata={"sample": {"stac:centroid": "binary"}})

    serialized = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", stac=taco.metadata.sample.STAC)),
    ).to_dict()
    serialized["taco:metadata"]["sample"]["stac:centroid"]["type"] = "string"
    with pytest.raises(ContractError, match="stac:centroid must have type binary"):
        taco.Contract.from_dict(serialized)


def test_collection_models() -> None:
    label = taco.metadata.collection.LabelClass(name="cloud", category=1)
    labels = taco.metadata.collection.Labels(classes=[label, "clear"])
    publications = taco.metadata.collection.Publications(
        publications=[taco.metadata.collection.Publication(doi="10.0/x", citation="A et al.")]
    )
    values = taco.CollectionMetadata(labels=labels, scientific=publications).flatten()
    assert values["labels:classes"][0]["category"] == "1"
    assert values["labels:num_classes"] == 2
    assert values["scientific:publications"][0]["doi"] == "10.0/x"


def test_major_tom_vector_batch() -> None:
    extension = taco.metadata.sample.MajorTOM(dist_km=100)
    result = extension.compute({"stac:centroid": [point(-76, -12), point(0, 0), point(100, 40)]})
    codes = result["code"]
    assert len(codes) == 3
    assert all(code.startswith("0100km_") for code in codes)
    assert len(set(codes)) == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dist_km": 0},
        {"latitude_range": (10, -10)},
        {"longitude_range": (10, -10)},
        {"sep": "a"},
    ],
)
def test_major_tom_configuration(kwargs) -> None:
    with pytest.raises(ValueError, match="must"):
        taco.metadata.sample.MajorTOM(**kwargs)


def test_major_tom_rejects_non_point() -> None:
    with pytest.raises(ValueError, match="WKB point"):
        taco.metadata.sample.MajorTOM().compute({"stac:centroid": [b"bad"]})


class FakeImage:
    def __init__(self, path: str) -> None:
        self.name = path

    def mosaic(self) -> FakeImage:
        return self

    def select(self, band: str) -> FakeImage:
        return self

    def rename(self, name: str) -> FakeImage:
        self.name = name
        return self

    def reduceRegions(self, *, collection, reducer, scale):
        name = self.name
        return SimpleNamespace(
            getInfo=lambda: {
                "features": [
                    {"properties": {"taco_index": feature["index"], name: feature["index"] + 0.5}}
                    for feature in collection
                ]
            }
        )


def test_geoenrich_batches_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        Feature=lambda geometry, values: {"index": values["taco_index"]},
        Geometry=SimpleNamespace(Point=lambda lon, lat: (lon, lat)),
        FeatureCollection=lambda values: values,
        Image=FakeImage,
        ImageCollection=FakeImage,
        Reducer=SimpleNamespace(mean=lambda: "mean", mode=lambda: "mode"),
    )
    monkeypatch.setitem(sys.modules, "ee", fake)
    extension = taco.metadata.sample.GeoEnrich(["elevation", "admin_countries"])
    result = extension.compute({"stac:centroid": [point(0, 0), point(1, 1)]})
    assert result == {"elevation": [0.5, 1.5], "admin_countries": ["0.5", "1.5"]}


def test_geoenrich_configuration() -> None:
    with pytest.raises(ValueError, match="unknown"):
        taco.metadata.sample.GeoEnrich(["nope"])
    with pytest.raises(ValueError, match="positive"):
        taco.metadata.sample.GeoEnrich(scale_m=0)
    with pytest.raises(ValueError, match="must not be empty"):
        taco.metadata.sample.GeoEnrich([])
    with pytest.raises(ValueError, match="unique"):
        taco.metadata.sample.GeoEnrich(["elevation", "elevation"])
    with pytest.raises(TypeError, match="sequence"):
        taco.metadata.sample.GeoEnrich("elevation")


def test_arrow_type_inference() -> None:
    class Nested(BaseModel):
        name: str
        score: float | None

    class Types(BaseModel):
        flag: bool
        payload: bytes
        day: date
        amount: Decimal
        choice: Literal["a", "b"]
        fixed: tuple[int, int]
        nested: Nested
        mapped: dict[str, int]
        exact: Annotated[float, pa.float32()]

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", types=Types)),
    )
    fields = contract.metadata["sample"]
    assert fields["types:flag"].type == "bool"
    assert fields["types:fixed"].type == "fixed_size_list<int64, 2>"
    assert fields["types:nested"].type == "struct<name: string, score: double>"
    assert fields["types:mapped"].type == "map<string, int64>"
    assert fields["types:exact"].type == "float"


@pytest.mark.parametrize("namespace", ["Bad", "taco", "internal", "a.b", "a-b"])
def test_invalid_namespaces(namespace: str) -> None:
    class Value(BaseModel):
        value: int

    with pytest.raises(ContractError):
        taco.Level("sample", **{namespace: Value})


def test_runtime_model_must_match_contract() -> None:
    class First(BaseModel):
        value: int

    class Second(BaseModel):
        value: int

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", value=First)),
    )
    with pytest.raises(SampleError, match="must be First"):
        contract.validate_sample(taco.Sample(assets=b"x", metadata=taco.Metadata(value=Second(value=1))))


def test_computed_fields_are_not_stored_implicitly() -> None:
    class Value(BaseModel):
        value: int

        @computed_field
        @property
        def doubled(self) -> int:
            return self.value * 2

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", value=Value)),
    )
    sample = contract.prepare_sample(taco.Sample(assets=b"x", metadata=taco.Metadata(value=Value(value=2))))
    assert sample.metadata == {"value:value": 2}


@dataclass(frozen=True)
class PlusOne(taco.DerivedMetadata):
    @property
    def requires(self) -> tuple[str, ...]:
        return ("base:value",)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.int64(), nullable=False)])

    def configuration(self) -> Mapping[str, object]:
        return {"values": (1, 2)}

    def compute(self, columns: Mapping[str, Sequence[object]]) -> Mapping[str, Sequence[object]]:
        return {"value": [int(value) + 1 for value in columns["base:value"]]}


def test_custom_derived_group(tmp_path) -> None:
    class Base(BaseModel):
        value: int

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", base=Base, next=PlusOne())),
    )
    assert contract.to_dict()["taco:derived"]["sample"]["next"]["configuration"] == {"values": [1, 2]}
    collection = taco.Collection(
        contract=contract,
        id="derived",
        dataset_version="1.0.0",
        description="Derived test",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "derived") as writer:
        writer.add(taco.Sample(assets=b"x", metadata=taco.Metadata(base=Base(value=2))))
        writer.run()
    from taco._view import open_view

    assert open_view(tmp_path / "derived").level("sample").column("next:value").to_pylist() == [3]


@dataclass(frozen=True)
class BatchSize(taco.DerivedMetadata):
    """Reads the whole batch, which is exactly what a derived group may not do."""

    @property
    def requires(self) -> tuple[str, ...]:
        return ("base:value",)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("size", pa.int64(), nullable=False)])

    def compute(self, columns: Mapping[str, Sequence[object]]) -> Mapping[str, Sequence[object]]:
        size = len(columns["base:value"])
        return {"size": [size] * size}


def test_derived_group_may_not_read_across_its_batch(tmp_path) -> None:
    class Base(BaseModel):
        value: int

    contract = taco.Contract(
        structure=None,
        metadata=taco.MetadataSchema(taco.Level("sample", base=Base, batch=BatchSize())),
    )
    collection = taco.Collection(
        contract=contract,
        id="derived",
        dataset_version="1.0.0",
        description="Derived test",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "batched", batch_size=4) as writer:
        for value in range(8):
            writer.add(taco.Sample(assets=b"x", metadata=taco.Metadata(base=Base(value=value))))
        with pytest.raises(taco.TacoError, match="depends on the other rows"):
            writer.run()
