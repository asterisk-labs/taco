import io
from datetime import datetime, timezone
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


contract = taco.Contract(
    structure=["image.npy", "label.npy"],
    metadata=[
        taco.Level(
            "sample",
            stac=taco.extensions.STAC(),
            chip=Chip,
            ml=taco.metadata.sample.Split,
            majortom=taco.extensions.MajorTOM(
                dist_km=100,
                latitude_range=(-20, 0),
                longitude_range=(-90, -60),
            ),
        ),
        taco.Level(
            "children",
            content=AssetContent,
            scaling=taco.metadata.asset.Scaling | None,
        ),
    ],
)

collection = taco.Collection(
    contract=contract,
    id="stac-segmentation",
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
        "site": (-77.04, -12.05),
        "crs": "EPSG:32718",
        "origin": (279_680.0, 8_667_920.0),
        "time": datetime(2024, 6, 15, 15, 26, tzinfo=timezone.utc),
        "cloud": 2.4,
        "cover": "urban",
        "split": "train",
    },
    {
        "scene": "S2A_20240710T151711",
        "site": (-71.97, -13.53),
        "crs": "EPSG:32719",
        "origin": (177_200.0, 8_502_080.0),
        "time": datetime(2024, 7, 10, 15, 17, tzinfo=timezone.utc),
        "cloud": 8.1,
        "cover": "vegetation",
        "split": "validation",
    },
    {
        "scene": "S2B_20240824T154619",
        "site": (-80.63, -5.19),
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
        easting, northing = chip["origin"]

        # The grid is enough: the writer computes the footprint and bbox from it.
        stac = taco.metadata.sample.STAC(
            proj_code=chip["crs"],
            proj_shape=image.shape[-2:],
            proj_transform=(10, 0, easting, 0, -10, northing),
            datetime=chip["time"],
        )
        assets = [
            taco.Asset(
                encode(image),
                path="image.npy",
                metadata=taco.Metadata(
                    content=AssetContent(role="input", bands=["B02", "B03", "B04", "B08"], nodata=0),
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
                ),
            ),
        ]
        writer.add(
            taco.Sample(
                id=f"chip-{index:04d}",
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
assert samples.num_rows == 3
assert dataset.sql('SELECT * FROM dataset ORDER BY "taco:sample_index"').equals(samples)
assert "majortom:code" in samples.column_names
assert samples.column("stac:bbox").null_count == 0
assert dataset.collection.to_dict()["labels:num_classes"] == 3
assert taco.validate("stac-segmentation.zip").ok
