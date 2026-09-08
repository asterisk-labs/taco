import io
import struct
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import numpy as np
import pyarrow as pa
from pydantic import BaseModel, Field

import taco


class OceanWindow(BaseModel):
    swot_cycle: Annotated[int, pa.int32()] = Field(description="SWOT repeat cycle")
    pass_number: Annotated[int, pa.int32()] = Field(description="Along-track pass number")
    direction: Literal["ascending", "descending"] = Field(description="Satellite pass direction")
    region: str = Field(description="Named ocean region")
    argo_float_id: str = Field(description="Collocated Argo platform identifier")
    collocation_distance_km: Annotated[float, pa.float32()] = Field(
        ge=0,
        description="Distance from the Argo profile to the swath centre",
    )


class AssetGroup(BaseModel):
    role: Literal["satellite", "in_situ"] = Field(description="Observation family")
    description: str = Field(description="Purpose of this folder")


class OceanVariables(BaseModel):
    standard_names: list[str] = Field(description="CF-style variable names stored in the asset")
    units: list[str] = Field(description="Units in the same order as standard_names")
    source_product: str = Field(description="Source product or sensor")
    processing_level: str = Field(description="Processing level of the source values")


class ArrayLayout(BaseModel):
    dimensions: list[str] = Field(description="Logical array dimensions")
    shape: list[int] = Field(description="Stored array shape")
    data_type: str = Field(description="NumPy data type")


class DatasetOrigin(BaseModel):
    project: str = Field(description="Project that inspired the example")
    url: str = Field(description="Project repository")
    dataset_doi: str = Field(description="Reference dataset DOI")
    note: str = Field(description="Relationship between the project and this example")


def encode(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


def point(longitude: float, latitude: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, longitude, latitude)


def polygon(ring: list[tuple[float, float]]) -> bytes:
    if ring[0] != ring[-1]:
        ring = [*ring, ring[0]]
    return struct.pack("<BIII", 1, 3, 1, len(ring)) + b"".join(
        struct.pack("<dd", longitude, latitude) for longitude, latitude in ring
    )


contract = taco.Contract(
    structure=[
        "satellite/sea_surface_height.npy",
        "satellite/sea_surface_temperature.npy",
        "satellite/wind_speed.npy",
        "in_situ/argo_profile.npy",
        "quality.npy",
    ],
    metadata=taco.MetadataSchema(
        taco.Level(
            "sample",
            istac=taco.metadata.sample.ISTAC,
            ocean=OceanWindow,
            ml=taco.metadata.sample.Split,
        ),
        taco.Level(
            "children",
            group=AssetGroup | None,
            variables=OceanVariables | None,
            array=ArrayLayout | None,
            raster=taco.metadata.asset.Raster | None,
            stats=taco.metadata.asset.RasterStats | None,
        ),
        taco.Level(
            "children/satellite",
            variables=OceanVariables,
            array=ArrayLayout,
            raster=taco.metadata.asset.Raster,
            stats=taco.metadata.asset.RasterStats,
        ),
        taco.Level(
            "children/in_situ",
            variables=OceanVariables,
            array=ArrayLayout,
        ),
    ),
)

collection = taco.Collection(
    contract=contract,
    id="oceantaco-istac",
    dataset_version="1.0.0",
    title="Synthetic SWOT and Argo collocations",
    description="Irregular ocean swaths with satellite predictors and collocated Argo profiles",
    licenses=["MIT"],
    providers=[{"name": "TACO examples", "roles": ["producer"]}],
    tasks=["regression"],
    keywords=["ISTAC", "OceanTACO", "SWOT", "Argo", "sensor fusion"],
    metadata=taco.CollectionMetadata(
        origin=DatasetOrigin(
            project="OceanTACO",
            url="https://github.com/nilsleh/oceanTACO",
            dataset_doi="10.57967/hf/8171",
            note="Workflow inspiration only; every value in this example is synthetic",
        ),
        scientific=taco.metadata.collection.Publications(
            publications=[
                taco.metadata.collection.Publication(
                    doi="10.5194/essd-2026-232",
                    citation=(
                        "Lehmann et al. (2026), OceanTACO: A Multi-Sensor Global Ocean Sea Surface State Dataset"
                    ),
                    summary="Motivation for the synthetic multi-sensor collocation workflow",
                )
            ]
        ),
        split=taco.metadata.collection.SplitStrategy(strategy="manual"),
    ),
)

windows = [
    {
        "centroid": (-43.8, 35.2),
        "ring": [(-45.3, 33.9), (-44.5, 33.6), (-42.1, 36.1), (-42.8, 36.7), (-43.9, 35.8)],
        "time": datetime(2024, 2, 11, 3, 42, tzinfo=timezone.utc),
        "cycle": 12,
        "pass": 143,
        "direction": "ascending",
        "region": "North Atlantic Current",
        "argo": "6903091",
        "distance": 8.3,
        "split": "train",
    },
    {
        "centroid": (64.4, -42.7),
        "ring": [(62.9, -44.1), (63.6, -44.5), (66.0, -41.6), (65.2, -41.0), (64.0, -42.0)],
        "time": datetime(2024, 3, 6, 18, 7, tzinfo=timezone.utc),
        "cycle": 13,
        "pass": 392,
        "direction": "descending",
        "region": "Agulhas Return Current",
        "argo": "5906468",
        "distance": 12.6,
        "split": "test",
    },
]

with taco.open_writer(collection, "oceantaco-istac.zip", overwrite=True) as writer:
    for index, window in enumerate(windows):
        rng = np.random.default_rng(100 + index)
        ssh = rng.normal(0.0, 0.18, size=(24, 8)).astype(np.float32)
        sst = rng.normal(17.0, 1.8, size=(24, 8)).astype(np.float32)
        wind = rng.uniform(1.0, 14.0, size=(24, 8)).astype(np.float32)
        argo = np.column_stack(
            (
                np.array([0, 10, 25, 50, 100], dtype=np.float32),
                np.linspace(float(sst.mean()), float(sst.mean()) - 4, 5, dtype=np.float32),
                np.linspace(35.1, 35.6, 5, dtype=np.float32),
            )
        )
        quality = (rng.random(ssh.shape) > 0.05).astype(np.uint8)

        def satellite_asset(
            path: str,
            array: np.ndarray,
            standard_name: str,
            units: str,
            source_product: str,
        ) -> taco.Asset:
            stats = [[float(array.min()), float(array.max()), float(array.mean())]]
            return taco.Asset(
                encode(array),
                path=path,
                metadata=taco.Metadata(
                    variables=OceanVariables(
                        standard_names=[standard_name],
                        units=[units],
                        source_product=source_product,
                        processing_level="L3",
                    ),
                    array=ArrayLayout(
                        dimensions=["along_track", "across_track"], shape=list(array.shape), data_type="float32"
                    ),
                    raster=taco.metadata.asset.Raster(resolution=2_000, num_bands=1, data_type="float32"),
                    stats=taco.metadata.asset.RasterStats(stats=stats),
                ),
            )

        assets = [
            satellite_asset(
                "satellite/sea_surface_height.npy",
                ssh,
                "sea_surface_height_above_geoid",
                "m",
                "SWOT L3",
            ),
            satellite_asset(
                "satellite/sea_surface_temperature.npy",
                sst,
                "sea_surface_temperature",
                "degree_Celsius",
                "satellite SST L3",
            ),
            satellite_asset(
                "satellite/wind_speed.npy",
                wind,
                "wind_speed",
                "m s-1",
                "satellite wind L3",
            ),
            taco.Asset(
                encode(argo),
                path="in_situ/argo_profile.npy",
                metadata=taco.Metadata(
                    variables=OceanVariables(
                        standard_names=["depth", "sea_water_temperature", "sea_water_salinity"],
                        units=["m", "degree_Celsius", "1e-3"],
                        source_product="Argo profile",
                        processing_level="quality controlled",
                    ),
                    array=ArrayLayout(dimensions=["depth", "variable"], shape=list(argo.shape), data_type="float32"),
                ),
            ),
            taco.Asset(
                encode(quality),
                path="quality.npy",
                metadata=taco.Metadata(
                    variables=OceanVariables(
                        standard_names=["quality_flag"],
                        units=["1"],
                        source_product="synthetic collocation",
                        processing_level="derived",
                    ),
                    array=ArrayLayout(
                        dimensions=["along_track", "across_track"],
                        shape=list(quality.shape),
                        data_type="uint8",
                    ),
                    raster=taco.metadata.asset.Raster(resolution=2_000, num_bands=1, data_type="uint8"),
                    stats=taco.metadata.asset.RasterStats(
                        stats=[[float(quality.min()), float(quality.max()), float(quality.mean())]]
                    ),
                ),
            ),
        ]

        # The clipped swath is not recoverable from one affine transform, so ISTAC stores its footprint.
        istac = taco.metadata.sample.ISTAC(
            crs="EPSG:4326",
            geometry=polygon(window["ring"]),
            centroid=point(*window["centroid"]),
            time_start=window["time"],
            time_end=window["time"] + timedelta(minutes=18),
        )
        writer.add(
            taco.Sample(
                assets=assets,
                folders=[
                    taco.Folder(
                        "satellite",
                        metadata=taco.Metadata(
                            group=AssetGroup(
                                role="satellite",
                                description="Gridded variables sampled on the irregular SWOT swath",
                            )
                        ),
                    ),
                    taco.Folder(
                        "in_situ",
                        metadata=taco.Metadata(
                            group=AssetGroup(
                                role="in_situ",
                                description="Profile selected by the space-time collocation query",
                            )
                        ),
                    ),
                ],
                metadata=taco.Metadata(
                    istac=istac,
                    ocean=OceanWindow(
                        swot_cycle=window["cycle"],
                        pass_number=window["pass"],
                        direction=window["direction"],
                        region=window["region"],
                        argo_float_id=window["argo"],
                        collocation_distance_km=window["distance"],
                    ),
                    ml=taco.metadata.sample.Split(split=window["split"]),
                ),
            )
        )
    writer.run()

dataset = taco.open_dataset("oceantaco-istac.zip")
samples = taco.read(dataset)
assets = taco.read(dataset, layout="long")
assert samples.num_rows == 2
assert assets.num_rows == 10
assert "istac:geometry" in samples.column_names
assert "stac:geotransform" not in samples.column_names
assert samples.column("istac:time_middle").null_count == 0
assert dataset.collection.extent is not None
assert dataset.collection.to_dict()["origin:project"] == "OceanTACO"
assert taco.validate("oceantaco-istac.zip").ok
