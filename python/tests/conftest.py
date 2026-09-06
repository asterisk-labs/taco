from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pyarrow as pa
import pytest
from pydantic import BaseModel, Field

import taco


class ML(BaseModel):
    split: str = Field(description="Dataset split")
    cloud_cover: float | None = Field(default=None, description="Cloud cover")
    tags: list[str] = Field(default_factory=list)


class Kind(BaseModel):
    kind: str


class Raster(BaseModel):
    resolution: Annotated[int, pa.int32()]


def point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def polygon(west: float, south: float, east: float, north: float) -> bytes:
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    return struct.pack("<BIII", 1, 3, 1, len(ring)) + b"".join(struct.pack("<dd", x, y) for x, y in ring)


STRUCTURE = ["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif", "extra*[0,3].png"]


@pytest.fixture
def contract() -> taco.Contract:
    return taco.Contract(
        structure=STRUCTURE,
        metadata=taco.MetadataSchema(
            taco.Level(
                "sample",
                stac=taco.metadata.sample.STAC,
                ml=ML,
                majortom=taco.metadata.sample.MajorTOM(dist_km=100),
            ),
            taco.Level("children", node=Kind),
            taco.Level("children/before", raster=Raster),
            taco.Level("children/after", raster=Raster),
        ),
    )


@pytest.fixture
def collection(contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id="tiny-change",
        dataset_version="1.0.0",
        description="Small change-detection fixture",
        licenses=["CC-BY-4.0"],
        providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
        tasks=["change-detection"],
        title="Tiny change",
        keywords=["fixture"],
        metadata=taco.CollectionMetadata(labels=taco.metadata.collection.Labels(classes=["clear", "change"])),
    )


@pytest.fixture
def make_sample(tmp_path: Path):
    def factory(index: int, n_extra: int = 0) -> taco.Sample:
        paths = ["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif"]
        paths.extend(f"extra{k}.png" for k in range(n_extra))
        assets = []
        for path in paths:
            source = tmp_path / "sources" / str(index) / path.replace("/", "_")
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(f"{index}:{path}".encode())
            if path.startswith("before/"):
                metadata = taco.Metadata(raster=Raster(resolution=10))
            elif path.startswith("after/"):
                metadata = taco.Metadata(raster=Raster(resolution=20))
            else:
                metadata = taco.Metadata(node=Kind(kind="label" if path == "mask.tif" else "extra"))
            assets.append(taco.Asset(source, path=path, metadata=metadata))
        center = point(-76 + index, -12 + index / 10)
        return taco.Sample(
            metadata=taco.Metadata(
                stac=taco.metadata.sample.STAC(
                    crs="EPSG:4326",
                    geometry=polygon(-76.1 + index, -12.1, -75.9 + index, -11.9),
                    centroid=center,
                    time_start=datetime(2024, 1, index + 1, tzinfo=timezone.utc),
                ),
                ml=ML(split="train" if index % 2 == 0 else "val", cloud_cover=index * 10.5, tags=["a"]),
            ),
            folders=[
                taco.Folder("before", metadata=taco.Metadata(node=Kind(kind="imagery"))),
                taco.Folder("after", metadata=taco.Metadata(node=Kind(kind="imagery"))),
            ],
            assets=assets,
        )

    return factory


@pytest.fixture
def archive(tmp_path: Path, collection: taco.Collection, make_sample) -> Path:
    path = tmp_path / "dataset.zip"
    with taco.open_writer(collection, path) as writer:
        writer.extend(make_sample(index, index % 3) for index in range(4))
        writer.run()
    return path


@pytest.fixture
def folder_dataset(tmp_path: Path, collection: taco.Collection, make_sample) -> Path:
    path = tmp_path / "folder"
    with taco.open_writer(collection, path) as writer:
        writer.extend(make_sample(index, index % 3) for index in range(4))
        writer.run()
    return path
