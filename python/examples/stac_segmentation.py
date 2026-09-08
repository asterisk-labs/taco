import io
import struct
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import numpy as np
import pyarrow as pa
from pydantic import BaseModel, Field

import taco


class Chip(BaseModel):
    source_scene: str = Field(description="Source scene identifier")
    cloud_cover: Annotated[float, pa.float32()] = Field(
        ge=0,
        le=100,
        description="Cloud cover over the chip in percent",
    )
    dominant_land_cover: Literal["water", "vegetation", "urban"] = Field(description="Most common label in the chip")


class AssetContent(BaseModel):
    role: Literal["input", "target"] = Field(description="Role in the learning task")
    bands: list[str] = Field(description="Bands stored in array order")
    nodata: float | None = Field(default=None, description="Value reserved for missing pixels")


class SourceCollection(BaseModel):
    name: str = Field(description="Input collection")
    bands: list[str] = Field(description="Selected source bands")
    note: str = Field(description="Relationship between the source and this example")


def encode(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


def point(longitude: float, latitude: float) -> bytes:
    # WKB keeps the example dependency-free. Centroids are always EPSG:4326.
    return struct.pack("<BIdd", 1, 1, longitude, latitude)


contract = taco.Contract(
    structure=["image.npy", "label.npy"],
    metadata=taco.MetadataSchema(
        taco.Level(
            "sample",
            stac=taco.metadata.sample.STAC,
            chip=Chip,
            ml=taco.metadata.sample.Split,
            majortom=taco.metadata.sample.MajorTOM(
                dist_km=100,
                latitude_range=(-20, 0),
                longitude_range=(-90, -60),
            ),
        ),
        taco.Level(
            "children",
            content=AssetContent,
            raster=taco.metadata.asset.Raster,
            stats=taco.metadata.asset.RasterStats | None,
            scaling=taco.metadata.asset.Scaling | None,
        ),
    ),
)

collection = taco.Collection(
    contract=contract,
    id="stac-segmentation",
    dataset_version="1.0.0",
    title="Synthetic Sentinel-2 land-cover chips",
    description="Regular multispectral chips and dense land-cover labels",
    licenses=["MIT"],
    providers=[{"name": "TACO examples", "roles": ["producer"]}],
    tasks=["semantic-segmentation"],
    keywords=["STAC", "Sentinel-2", "land cover", "regular chunks"],
    metadata=taco.CollectionMetadata(
        source=SourceCollection(
            name="Sentinel-2 MSI",
            bands=["B02", "B03", "B04", "B08"],
            note="The arrays are synthetic; only their shape and scale resemble reflectance chips",
        ),
        labels=taco.metadata.collection.Labels(
            classes=[
                taco.metadata.collection.LabelClass(name="water", category=0),
                taco.metadata.collection.LabelClass(name="vegetation", category=1),
                taco.metadata.collection.LabelClass(name="urban", category=2),
            ],
            description="Dense land-cover classes stored in label.npy",
        ),
        optical=taco.metadata.collection.Optical(
            sensor="Sentinel-2 MSI",
            bands=[
                taco.metadata.collection.SpectralBand(name="B02", index=0, common_name="blue"),
                taco.metadata.collection.SpectralBand(name="B03", index=1, common_name="green"),
                taco.metadata.collection.SpectralBand(name="B04", index=2, common_name="red"),
                taco.metadata.collection.SpectralBand(name="B08", index=3, common_name="nir"),
            ],
        ),
        split=taco.metadata.collection.SplitStrategy(strategy="manual"),
    ),
)

chips = [
    {
        "scene": "S2B_20240615T152629",
        "centroid": (-77.04, -12.05),
        "crs": "EPSG:32718",
        "origin": (279_680.0, 8_667_920.0),
        "time": datetime(2024, 6, 15, 15, 26, tzinfo=timezone.utc),
        "cloud": 2.4,
        "cover": "urban",
        "split": "train",
    },
    {
        "scene": "S2A_20240710T151711",
        "centroid": (-71.97, -13.53),
        "crs": "EPSG:32719",
        "origin": (177_200.0, 8_502_080.0),
        "time": datetime(2024, 7, 10, 15, 17, tzinfo=timezone.utc),
        "cloud": 8.1,
        "cover": "vegetation",
        "split": "validation",
    },
    {
        "scene": "S2B_20240824T154619",
        "centroid": (-80.63, -5.19),
        "crs": "EPSG:32717",
        "origin": (541_120.0, 9_426_880.0),
        "time": datetime(2024, 8, 24, 15, 46, tzinfo=timezone.utc),
        "cloud": 0.7,
        "cover": "water",
        "split": "test",
    },
]

with taco.open_writer(collection, "stac-segmentation.zip", overwrite=True) as writer:
    for index, chip in enumerate(chips):
        rng = np.random.default_rng(index)
        image = rng.integers(1, 10_001, size=(4, 32, 32), dtype=np.uint16)
        class_names = ("water", "vegetation", "urban")
        class_scores = image[:3].astype(np.float32)
        class_scores[class_names.index(chip["cover"])] += 5_000
        label = np.argmax(class_scores, axis=0).astype(np.uint8)
        dominant_land_cover = class_names[int(np.bincount(label.ravel()).argmax())]
        band_stats = [[float(band.min()), float(band.max()), float(band.mean())] for band in image]
        longitude, latitude = chip["centroid"]
        easting, northing = chip["origin"]

        # STAC is enough here because the affine grid reconstructs every footprint.
        stac = taco.metadata.sample.STAC(
            crs=chip["crs"],
            tensor_shape=image.shape,
            geotransform=(easting, 10, 0, northing, 0, -10),
            centroid=point(longitude, latitude),
            time_start=chip["time"],
            time_end=chip["time"] + timedelta(minutes=10),
        )
        assets = [
            taco.Asset(
                encode(image),
                path="image.npy",
                metadata=taco.Metadata(
                    content=AssetContent(role="input", bands=["B02", "B03", "B04", "B08"], nodata=0),
                    raster=taco.metadata.asset.Raster(resolution=10, num_bands=4, data_type="uint16"),
                    stats=taco.metadata.asset.RasterStats(stats=band_stats),
                    scaling=taco.metadata.asset.Scaling(
                        scale_factor=[0.0001] * 4,
                        scale_offset=[0.0] * 4,
                    ),
                ),
            ),
            taco.Asset(
                encode(label),
                path="label.npy",
                metadata=taco.Metadata(
                    content=AssetContent(role="target", bands=["land_cover"], nodata=255),
                    raster=taco.metadata.asset.Raster(resolution=10, num_bands=1, data_type="uint8"),
                ),
            ),
        ]
        writer.add(
            taco.Sample(
                assets=assets,
                metadata=taco.Metadata(
                    stac=stac,
                    chip=Chip(
                        source_scene=chip["scene"],
                        cloud_cover=chip["cloud"],
                        dominant_land_cover=dominant_land_cover,
                    ),
                    ml=taco.metadata.sample.Split(split=chip["split"]),
                ),
            )
        )
    writer.run()

dataset = taco.open_dataset("stac-segmentation.zip")
samples = taco.read(dataset)
assets = taco.read(dataset, layout="long")
assert samples.num_rows == 3
assert assets.num_rows == 6
assert "majortom:code" in samples.column_names
assert samples.column("stac:time_middle").null_count == 0
assert dataset.collection.to_dict()["labels:num_classes"] == 3
assert taco.validate("stac-segmentation.zip").ok
