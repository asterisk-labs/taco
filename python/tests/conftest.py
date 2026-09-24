from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pyarrow as pa
import pytest
from pydantic import BaseModel, Field

import taco


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TACO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("TACO_CACHE_REFRESH", raising=False)
    monkeypatch.delenv("TACO_CACHE_SIZE", raising=False)


class ML(BaseModel):
    split: str = Field(description="Dataset split")
    cloud_cover: float | None = Field(default=None, description="Cloud cover")
    tags: list[str] = Field(default_factory=list)


class Kind(BaseModel):
    kind: str


class AssetInfo(BaseModel):
    resolution: Annotated[int, pa.int32()]


STRUCTURE = ["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif", "extra*[1,3].png"]


@pytest.fixture
def contract() -> taco.Contract:
    return taco.Contract(
        structure=STRUCTURE,
        metadata=taco.MetadataSchema(
            taco.Level(
                "sample",
                stac=taco.extensions.STAC(),
                ml=ML,
                majortom=taco.extensions.MajorTOM(dist_km=100),
            ),
            taco.Level("children", node=Kind),
            taco.Level("children/before", file=AssetInfo),
            taco.Level("children/after", file=AssetInfo),
        ),
    )


@pytest.fixture
def collection(contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id="tiny-change",
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
    def factory(index: int, n_extra: int = 1, *, after_resolution: int = 20) -> taco.Sample:
        paths = ["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif"]
        paths.extend(f"extra{k}.png" for k in range(n_extra))
        assets = []
        for path in paths:
            source = tmp_path / "sources" / str(index) / path.replace("/", "_")
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(f"{index}:{path}".encode())
            if path.startswith("before/"):
                metadata = taco.Metadata(file=AssetInfo(resolution=10))
            elif path.startswith("after/"):
                metadata = taco.Metadata(file=AssetInfo(resolution=after_resolution))
            else:
                metadata = taco.Metadata(node=Kind(kind="label" if path == "mask.tif" else "extra"))
            assets.append(taco.Asset(source, path=path, metadata=metadata))
        longitude, latitude = -76 + index, -12 + index / 10
        return taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(
                stac=taco.metadata.sample.STAC(
                    crs="EPSG:4326",
                    tensor_shape=(13, 256, 256),
                    geotransform=(longitude - 0.1, 0.2 / 256, 0, latitude + 0.1, 0, -0.2 / 256),
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
        writer.extend(make_sample(index, max(1, index % 3)) for index in range(4))
        writer.run()
    return path


@pytest.fixture
def folder_dataset(tmp_path: Path, collection: taco.Collection, make_sample) -> Path:
    path = tmp_path / "folder"
    with taco.open_writer(collection, path) as writer:
        writer.extend(make_sample(index, max(1, index % 3)) for index in range(4))
        writer.run()
    return path
