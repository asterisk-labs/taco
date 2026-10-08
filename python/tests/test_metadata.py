from __future__ import annotations

import copy
import struct
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Annotated, ClassVar, Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel, computed_field

import taco
from taco.errors import ContractError, SampleError

from . import models


def point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def centroid(x: float, y: float) -> dict[str, float]:
    return {"lon": x, "lat": y}


def test_extensions_are_grouped_by_scope() -> None:
    assert taco.extensions.sample.__all__ == ["geoenrich", "majortom", "rumi", "split", "stac"]
    assert taco.extensions.collection.__all__ == []
    assert taco.extensions.sample.stac.STAC is taco.extensions.sample.stac.models.STAC
    assert taco.extensions.sample.stac.STAC.__taco_scopes__ == frozenset({"sample"})
    for name in ("STAC", "Spatial", "Rumi", "MajorTOM", "GeoEnrich", "stac", "rumi", "majortom", "geoenrich"):
        assert not hasattr(taco.extensions, name)


def test_metadata_module_is_gone() -> None:
    with pytest.raises(AttributeError, match="no attribute 'metadata'"):
        _ = taco.metadata  # type: ignore[attr-defined]
    assert "metadata" not in dir(taco)


GRID = {"proj_code": "EPSG:4326", "proj_shape": (256, 256), "proj_transform": (1 / 256, 0, -0.5, 0, -1 / 256, 0.5)}


def test_stac_time_rules() -> None:
    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    later = datetime(2024, 1, 3, tzinfo=timezone.utc)
    taco.extensions.sample.stac.STAC(**GRID, datetime=day)
    taco.extensions.sample.stac.STAC(**GRID, start_datetime=day, end_datetime=later)
    taco.extensions.sample.stac.STAC(**GRID, datetime=day, start_datetime=day, end_datetime=later)
    taco.extensions.sample.stac.STAC(**GRID, start_datetime=day, end_datetime=day)
    with pytest.raises(ValueError, match="datetime is required"):
        taco.extensions.sample.stac.STAC(**GRID)
    with pytest.raises(ValueError, match="given together"):
        taco.extensions.sample.stac.STAC(**GRID, start_datetime=day)
    with pytest.raises(ValueError, match="must not be after"):
        taco.extensions.sample.stac.STAC(**GRID, start_datetime=later, end_datetime=day)


def test_profile_fields_follow_stac() -> None:
    location = ("geometry", "bbox", "centroid")
    grid = ("proj_code", "proj_shape", "proj_transform")
    times = ("datetime", "start_datetime", "end_datetime")
    assert tuple(taco.extensions.sample.stac.STAC.model_fields) == (*location, *times, *grid)
    assert tuple(taco.extensions.sample.stac.Spatial.model_fields) == (*location, *grid)
    assert tuple(taco.extensions.sample.stac.Temporal.model_fields) == times
    assert taco.extensions.sample.stac.Spatial.__taco_namespace__ == "spatial"
    assert taco.extensions.sample.stac.Temporal.__taco_namespace__ == "temporal"
    assert taco.extensions.sample.stac.STAC.__taco_namespace__ == "stac"


def test_stac_location_rules() -> None:
    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="geometry is required"):
        taco.extensions.sample.stac.STAC(datetime=day)
    with pytest.raises(ValueError, match="given together"):
        taco.extensions.sample.stac.STAC(proj_code="EPSG:4326", proj_shape=(2, 2), datetime=day)
    with pytest.raises(ValueError, match="AUTHORITY:CODE"):
        taco.extensions.sample.stac.STAC(**{**GRID, "proj_code": "4326"}, datetime=day)
    with pytest.raises(ValueError, match="positive"):
        taco.extensions.sample.stac.STAC(**{**GRID, "proj_shape": (0, 256)}, datetime=day)
    with pytest.raises(ValueError, match="at most 2"):
        taco.extensions.sample.stac.STAC(**{**GRID, "proj_shape": (3, 256, 256)}, datetime=day)
    with pytest.raises(ValueError, match="collapse"):
        taco.extensions.sample.stac.STAC(**{**GRID, "proj_transform": (1, 2, 0, 2, 4, 0)}, datetime=day)
    with pytest.raises(ValueError, match="not valid WKB"):
        taco.extensions.sample.stac.STAC(geometry=b"bad", datetime=day)
    with pytest.raises(ValueError, match="EPSG:4326 bounds"):
        taco.extensions.sample.stac.STAC(geometry=point(200, 0), datetime=day)
    with pytest.raises(ValueError, match="requires geometry"):
        taco.extensions.sample.stac.STAC(**GRID, bbox=(0, 0, 1, 1), datetime=day)
    with pytest.raises(ValueError, match="does not match"):
        taco.extensions.sample.stac.STAC(geometry=point(1, 2), bbox=(0, 0, 1, 1), datetime=day)
    taco.extensions.sample.stac.STAC(geometry=point(1, 2), bbox=(1, 2, 1, 2), datetime=day)


def test_a_profile_class_computes_its_centroid() -> None:
    class Checked(taco.extensions.sample.stac.STAC):
        pass

    for model in (taco.extensions.sample.stac.STAC, Checked, taco.extensions.sample.stac.STAC | None):
        contract = taco.Contract(structure=["a.tif"], metadata=[taco.Level("sample", stac=model)])
        assert contract.operations["sample"]["stac"]["produces"] == ["stac:centroid"]
    temporal = taco.Contract(
        structure=["a.tif"], metadata=[taco.Level("sample", temporal=taco.extensions.sample.stac.Temporal)]
    )
    assert temporal.operations == {}


def test_spatial_models_require_canonical_namespaces() -> None:
    with pytest.raises(ContractError, match="must use metadata namespace 'stac'"):
        taco.Level("sample", location=taco.extensions.sample.stac.STAC)
    with pytest.raises(ContractError, match="must use metadata namespace 'spatial'"):
        taco.Level("sample", stac=taco.extensions.sample.stac.Spatial)


def test_contract_checks_profile_columns() -> None:
    with pytest.raises(ContractError, match="must choose one metadata profile, got SPATIAL, TEMPORAL"):
        taco.Contract(
            structure=["data.bin"],
            metadata=[
                taco.Level(
                    "sample", spatial=taco.extensions.sample.stac.Spatial, temporal=taco.extensions.sample.stac.Temporal
                )
            ],
        )
    with pytest.raises(ContractError, match=r"STAC metadata.*missing fields"):
        taco.Contract(structure=["data.bin"], metadata={"sample": {"stac:geometry": "binary"}})
    with pytest.raises(ContractError, match=r"SPATIAL metadata.*missing fields"):
        taco.Contract(structure=["data.bin"], metadata={"sample": {"spatial:geometry": "binary"}})
    with pytest.raises(ContractError, match=r"TEMPORAL metadata.*missing fields"):
        taco.Contract(
            structure=["data.bin"],
            metadata={"sample": {"temporal:datetime": {"type": "timestamp[us, UTC]", "nullable": True}}},
        )

    serialized = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.sample.stac.STAC)],
    ).to_dict()
    assert taco.Contract.from_dict(serialized).to_dict() == serialized
    changed = copy.deepcopy(serialized)
    changed["taco:metadata"]["sample"]["stac:bbox"]["type"] = "fixed_size_list<double, 4>"
    with pytest.raises(ContractError, match="stac:bbox must have type list<double>"):
        taco.Contract.from_dict(changed)
    changed = copy.deepcopy(serialized)
    del changed["taco:metadata"]["sample"]["stac:end_datetime"]
    with pytest.raises(ContractError, match=r"STAC metadata.*missing fields \['end_datetime'\]"):
        taco.Contract.from_dict(changed)


def test_derived_centroid_dependency_is_configurable() -> None:
    majortom = taco.extensions.sample.majortom.MajorTOM(centroid="spatial:centroid")
    geoenrich = taco.extensions.sample.geoenrich.GeoEnrich(
        ["elevation"], backend="earthengine", centroid="spatial:centroid"
    )
    assert majortom.requires == ("spatial:centroid",)
    assert majortom.configuration()["centroid"] == "spatial:centroid"
    assert majortom.collection_metadata()["centroid"] == "spatial:centroid"
    assert geoenrich.requires == ("spatial:centroid",)
    assert geoenrich.configuration()["centroid"] == "spatial:centroid"
    with pytest.raises(ValueError, match="ending in ':centroid'"):
        taco.extensions.sample.majortom.MajorTOM(centroid="spatial:geometry")


def test_collection_models() -> None:
    label = models.LabelClass(name="cloud", category=1)
    labels = models.Labels(classes=[label, "clear"])
    publications = models.Publications(publications=[models.Publication(doi="10.0/x", citation="A et al.")])
    collection = taco.Collection(
        contract=taco.Contract(structure=["data.bin"]),
        id="models",
        description="Collection models",
        licenses=["MIT"],
        providers=["me"],
        labels=labels,
        scientific=publications,
    )
    values = collection.to_dict()
    assert values["labels:classes"][0]["category"] == "1"
    assert values["labels:num_classes"] == 2
    assert values["scientific:publications"][0]["doi"] == "10.0/x"


def test_major_tom_vector_batch() -> None:
    extension = taco.extensions.sample.majortom.MajorTOM(dist_km=100)
    result = extension.compute({"stac:centroid": [centroid(-76, -12), centroid(0, 0), centroid(100, 40)]})
    codes = result["code"]
    assert len(codes) == 3
    assert all(code.startswith("MT100km_") for code in codes)
    assert len(set(codes)) == 3


def test_major_tom_preserves_fractional_grid_distances() -> None:
    extension = taco.extensions.sample.majortom.MajorTOM(
        dist_km=0.5,
        extra={"coarse": 320.5, "fine": 320.9},
    )
    descriptions = [field.metadata[b"description"].decode() for field in extension.fields]
    assert descriptions == [
        "MajorTOM spherical grid cell identifier at 0.5 km",
        "MajorTOM spherical grid cell identifier at 320.5 km",
        "MajorTOM spherical grid cell identifier at 320.9 km",
    ]
    result = taco.extensions.sample.majortom.MajorTOM(dist_km=100.4).compute({"stac:centroid": [centroid(-76, -12)]})
    assert result["code"][0].startswith("MT100.4km_")


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
        taco.extensions.sample.majortom.MajorTOM(**kwargs)


def test_major_tom_rejects_non_point() -> None:
    with pytest.raises(ValueError, match="must be a point"):
        taco.extensions.sample.majortom.MajorTOM().compute({"stac:centroid": [b"bad"]})


class FakeImage:
    calls: list[tuple[str, int, float, str]] = []
    unmask_values: list[float] = []

    def __init__(self, path) -> None:
        self.names = [image.names[0] for image in path] if isinstance(path, list) else [str(path)]

    def mosaic(self) -> FakeImage:
        return self

    def select(self, band: str) -> FakeImage:
        return self

    def rename(self, name: str) -> FakeImage:
        self.names = [name]
        return self

    def unmask(self, value: float) -> FakeImage:
        self.unmask_values.append(value)
        return self

    def reduceRegions(self, *, collection, reducer, scale, crs):
        self.calls.append((reducer, len(collection), scale, crs))

        def properties(feature):
            values = {"taco_index": feature["index"]}
            if reducer == "mode":
                values["mode"] = 0 if feature["index"] == 0 else 65535
            else:
                values.update(dict.fromkeys(self.names, feature["index"] + 0.5))
            return values

        return SimpleNamespace(
            getInfo=lambda: {"features": [{"properties": properties(feature)} for feature in collection]}
        )


def fake_earth_engine() -> SimpleNamespace:
    return SimpleNamespace(
        Feature=lambda geometry, values: {"index": values["taco_index"]},
        Geometry=SimpleNamespace(Point=lambda lon, lat: (lon, lat)),
        FeatureCollection=lambda values: values,
        Image=FakeImage,
        ImageCollection=FakeImage,
        Reducer=SimpleNamespace(mean=lambda: "mean", mode=lambda: "mode"),
    )


def test_geoenrich_batches_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeImage.calls.clear()
    FakeImage.unmask_values.clear()
    monkeypatch.setitem(sys.modules, "ee", fake_earth_engine())
    extension = taco.extensions.sample.geoenrich.GeoEnrich(
        ["elevation", "admin_countries"],
        backend="earthengine",
        batch_size=1,
        max_concurrency=1,
    )
    result = extension.compute({"stac:centroid": [centroid(0, 0), centroid(1, 1)]})
    assert result == {
        "elevation": [0.5, 1.5],
        "admin_countries": ["Afghanistan", "Ocean/Sea/Lakes"],
    }
    assert all(type(value) is float for value in result["elevation"])
    assert FakeImage.unmask_values == [65535]
    assert FakeImage.calls == [
        ("mean", 1, 5120.0, "EPSG:4326"),
        ("mode", 1, 5120.0, "EPSG:4326"),
        ("mean", 1, 5120.0, "EPSG:4326"),
        ("mode", 1, 5120.0, "EPSG:4326"),
    ]


def test_geoenrich_replaces_missing_admin_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ee", fake_earth_engine())
    monkeypatch.setattr(
        taco.extensions.sample.geoenrich.extension, "_admin_names", lambda level: {0: "Afghanistan", 53343: None}
    )

    def reduce_regions(self, *, collection, reducer, scale, crs):
        return SimpleNamespace(
            getInfo=lambda: {
                "features": [{"properties": {"taco_index": feature["index"], "mode": 53343}} for feature in collection]
            }
        )

    monkeypatch.setattr(FakeImage, "reduceRegions", reduce_regions)
    extension = taco.extensions.sample.geoenrich.GeoEnrich(["admin_districts"], backend="earthengine")
    result = extension.compute({"stac:centroid": [centroid(63.794370059438705, 36.06268468013294)]})

    assert result == {"admin_districts": ["Unknown"]}


def test_geoenrich_converts_units_and_keeps_missing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ee", fake_earth_engine())
    FakeImage.unmask_values.clear()
    raw = {
        0: {"temperature": 300.0, "soil_ph": 65, "population": 1250.5},
        1: {"population": 0.0},
    }

    def reduce_regions(self, *, collection, reducer, scale, crs):
        return SimpleNamespace(
            getInfo=lambda: {
                "features": [
                    {"properties": {"taco_index": feature["index"], **raw[feature["index"]]}} for feature in collection
                ]
            }
        )

    monkeypatch.setattr(FakeImage, "reduceRegions", reduce_regions)
    extension = taco.extensions.sample.geoenrich.GeoEnrich(
        ["temperature", "soil_ph", "population"], backend="earthengine"
    )
    result = extension.compute({"stac:centroid": [centroid(0, 0), centroid(1, 1)]})

    assert result["temperature"] == [pytest.approx(26.85, abs=1e-5), None]
    assert result["soil_ph"] == [6.5, None]
    assert result["population"] == [1250.5, 0.0]
    assert FakeImage.unmask_values == [0.0]


def test_geoenrich_retries_failed_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ee", fake_earth_engine())
    delays: list[int] = []
    monkeypatch.setattr(taco.extensions.sample.geoenrich.extension.time, "sleep", delays.append)
    failures = [RuntimeError("Too many concurrent aggregations")]

    def reduce_regions(self, *, collection, reducer, scale, crs):
        def get_info():
            if failures:
                raise failures.pop()
            return {"features": [{"properties": {"taco_index": 0, "elevation": 12.0}}]}

        return SimpleNamespace(getInfo=get_info)

    monkeypatch.setattr(FakeImage, "reduceRegions", reduce_regions)
    result = taco.extensions.sample.geoenrich.GeoEnrich(["elevation"], backend="earthengine").compute(
        {"stac:centroid": [centroid(0, 0)]}
    )

    assert result == {"elevation": [12.0]}
    assert delays == [1]


def test_geoenrich_ships_admin_names() -> None:
    from taco.extensions.sample.geoenrich.extension import _admin_names

    for level in (0, 1, 2):
        names = _admin_names(level)
        assert names
        assert all(isinstance(code, int) for code in names)


def test_geoenrich_configuration() -> None:
    assert taco.extensions.sample.geoenrich.GeoEnrich.__taco_complete_level__
    assert not taco.extensions.sample.majortom.MajorTOM.__taco_complete_level__
    with pytest.raises(ValueError, match="unknown"):
        taco.extensions.sample.geoenrich.GeoEnrich(["nope"])
    with pytest.raises(ValueError, match="positive"):
        taco.extensions.sample.geoenrich.GeoEnrich(scale_m=0)
    with pytest.raises(ValueError, match="must not be empty"):
        taco.extensions.sample.geoenrich.GeoEnrich([])
    with pytest.raises(ValueError, match="unique"):
        taco.extensions.sample.geoenrich.GeoEnrich(["elevation", "elevation"])
    with pytest.raises(TypeError, match="sequence"):
        taco.extensions.sample.geoenrich.GeoEnrich("elevation")
    with pytest.raises(ValueError, match="batch_size"):
        taco.extensions.sample.geoenrich.GeoEnrich(batch_size=0)
    with pytest.raises(ValueError, match="max_concurrency"):
        taco.extensions.sample.geoenrich.GeoEnrich(max_concurrency=0)
    with pytest.raises(ValueError, match="backend"):
        taco.extensions.sample.geoenrich.GeoEnrich(backend="unknown")
    with pytest.raises(ValueError, match="index_url"):
        taco.extensions.sample.geoenrich.GeoEnrich(index_url="")

    fields = taco.extensions.sample.geoenrich.GeoEnrich(["gdp", "admin_countries"]).fields
    assert fields.field("gdp").type == pa.float32()
    assert fields.field("admin_countries").type == pa.string()
    assert fields.field("gdp").nullable
    assert not fields.field("admin_countries").nullable
    assert fields.field("admin_countries").metadata[b"description"]

    default = taco.extensions.sample.geoenrich.GeoEnrich(["elevation"])
    assert default.requires == ("majortom:code",)
    assert default.configuration() == {
        "variables": ["elevation"],
        "backend": "majortom-index",
        "code": "majortom:code",
        "index_url": "https://data.source.coop/major-tom/index/global.parquet",
    }
    assert default.collection_metadata() == {
        "backend": "majortom-index",
        "index_url": "https://data.source.coop/major-tom/index/global.parquet",
    }

    earthengine = taco.extensions.sample.geoenrich.GeoEnrich(["elevation"], backend="earthengine")
    assert earthengine.requires == ("stac:centroid",)
    assert earthengine.collection_metadata() == {}


def test_geoenrich_reads_majortom_index_in_input_order(tmp_path) -> None:
    index = tmp_path / "global.parquet"
    pq.write_table(
        pa.table(
            {
                "id": ["MT10km_0000U_0000R", "MT10km_0000U_0001R"],
                "geoenrich:elevation": pa.array([12.25, None], type=pa.float32()),
                "geoenrich:admin_countries": ["Peru", "Ecuador"],
            }
        ),
        index,
    )
    extension = taco.extensions.sample.geoenrich.GeoEnrich(
        ["elevation", "admin_countries"],
        index_url=str(index),
    )

    assert extension.compute(
        {
            "majortom:code": [
                "MT10km_0000U_0001R",
                "MT10km_0000U_0000R",
                "MT10km_0000U_0001R",
            ]
        }
    ) == {
        "elevation": [None, 12.25, None],
        "admin_countries": ["Ecuador", "Peru", "Ecuador"],
    }


def test_geoenrich_majortom_index_rejects_missing_codes(tmp_path) -> None:
    index = tmp_path / "global.parquet"
    pq.write_table(
        pa.table({"id": ["MT10km_0000U_0000R"], "geoenrich:elevation": [12.25]}),
        index,
    )
    extension = taco.extensions.sample.geoenrich.GeoEnrich(["elevation"], index_url=str(index))

    with pytest.raises(ValueError, match="MT10km_9999U_9999R"):
        extension.compute({"majortom:code": ["MT10km_9999U_9999R"]})


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
        structure=["data.bin"],
        metadata=[taco.Level("sample", types=Types)],
    )
    fields = contract.metadata["sample"]
    assert fields["types:flag"].type == "bool"
    assert fields["types:fixed"].type == "fixed_size_list<int64, 2>"
    assert fields["types:nested"].type == "struct<name: string, score: double?>"
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
        structure=["data.bin"],
        metadata=[taco.Level("sample", value=First)],
    )
    with pytest.raises(SampleError, match="must be First"):
        contract.validate_sample(taco.Sample(id="u21", assets=b"x", metadata=taco.Metadata(value=Second(value=1))))


def test_computed_fields_are_not_stored_implicitly() -> None:
    class Value(BaseModel):
        value: int

        @computed_field
        @property
        def doubled(self) -> int:
            return self.value * 2

    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", value=Value)],
    )
    sample = contract.prepare_sample(taco.Sample(id="u22", assets=b"x", metadata=taco.Metadata(value=Value(value=2))))
    assert sample.id == "u22"
    assert sample.metadata == {"value:value": 2}


@dataclass(frozen=True)
class PlusOne(taco.Extension):
    @property
    def requires(self) -> tuple[str, ...]:
        return ("base:value",)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.int64(), nullable=False)])

    def configuration(self) -> Mapping[str, object]:
        return {"values": (1, 2)}

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[object]]:
        return {"value": [int(value) + 1 for value in context.columns["base:value"]]}


def test_custom_derived_group(tmp_path) -> None:
    class Base(BaseModel):
        value: int

    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", base=Base, next=PlusOne())],
    )
    assert contract.operations["sample"]["next"]["configuration"] == {"values": [1, 2]}
    collection = taco.Collection(
        contract=contract,
        id="derived",
        description="Derived test",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "derived") as writer:
        writer.add(taco.Sample(id="u23", assets=b"x", metadata=taco.Metadata(base=Base(value=2))))
        writer.run()
    from taco.container.view import open_view

    dataset = open_view(tmp_path / "derived")
    assert dataset.level("sample").column("next:value").to_pylist() == [3]
    assert "taco:derived" not in dataset.collection_json


@dataclass(frozen=True)
class BatchSize(taco.Extension):
    """Inspect the complete batch instead of one row."""

    @property
    def requires(self) -> tuple[str, ...]:
        return ("base:value",)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("size", pa.int64(), nullable=False)])

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[object]]:
        return {"size": [len(context)] * len(context)}


@dataclass(frozen=True)
class RowLocalPlusOne(PlusOne):
    __taco_row_local__: ClassVar[bool] = True
    calls: ClassVar[int] = 0

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[object]]:
        type(self).calls += 1
        return super().run(context)


def test_row_local_extensions_skip_the_batch_check(tmp_path) -> None:
    class Base(BaseModel):
        value: int

    RowLocalPlusOne.calls = 0
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", base=Base, next=RowLocalPlusOne())],
    )
    collection = taco.Collection(
        contract=contract,
        id="derived",
        description="Derived test",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "batched", batch_size=4) as writer:
        for value in range(8):
            writer.add(taco.Sample(id=f"u25-{value}", assets=b"x", metadata=taco.Metadata(base=Base(value=value))))
        writer.run()
    assert RowLocalPlusOne.calls == 2


def test_derived_group_may_not_read_across_its_batch(tmp_path) -> None:
    class Base(BaseModel):
        value: int

    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", base=Base, batch=BatchSize())],
    )
    collection = taco.Collection(
        contract=contract,
        id="derived",
        description="Derived test",
        licenses=["MIT"],
        providers=["me"],
        tasks=["other"],
    )
    with taco.open_writer(collection, tmp_path / "batched", batch_size=4) as writer:
        for value in range(8):
            writer.add(taco.Sample(id=f"u24-{value}", assets=b"x", metadata=taco.Metadata(base=Base(value=value))))
        with pytest.raises(taco.TacoError, match="depends on the other rows"):
            writer.run()
