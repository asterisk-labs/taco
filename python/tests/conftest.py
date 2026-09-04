from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

import pytest

import taco


def wkb_point(x: float, y: float) -> bytes:
    """A 2D point as little-endian WKB, to fill a binary metadata field."""
    return struct.pack("<BIdd", 1, 1, float(x), float(y))


def wkb_bbox(west: float, south: float, east: float, north: float) -> bytes:
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    return struct.pack("<BIII", 1, 3, 1, len(ring)) + b"".join(struct.pack("<dd", x, y) for x, y in ring)


STRUCTURE = ["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif", "extra*[0,3].png"]


@pytest.fixture
def contract() -> taco.Contract:
    return taco.Contract(
        structure=STRUCTURE,
        metadata={
            "collection": {
                "stac:crs": ["string", "Coordinate reference system"],
                "stac:geometry": ["binary", "Footprint in EPSG:4326 (WKB)"],
                "stac:centroid": ["binary", "Centroid in EPSG:4326 (WKB)"],
                "stac:time_start": ["timestamp[us]", "Acquisition start"],
                "stac:time_end": ["timestamp[us]", "Acquisition end"],
                "split": ["string", "Dataset split"],
                "cloud_cover": ["double", "Cloud cover percentage"],
                "tags": ["list<string>", "Free tags"],
            },
            "sample": {"kind": ["string", "Child role"]},
            "sample/before": {"resolution": ["int32", "Spatial resolution in metres"]},
            "sample/after": {"resolution": ["int32", "Spatial resolution in metres"]},
        },
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
        extra={"labels:num_classes": 2},
    )


@pytest.fixture
def make_sample(tmp_path: Path):
    """Factory producing valid samples with ``n_extra`` variable-leaf files."""

    def factory(index: int, n_extra: int = 0, *, lon: float | None = None, lat: float | None = None) -> taco.Sample:
        source_dir = tmp_path / "sources" / str(index)
        source_dir.mkdir(parents=True, exist_ok=True)
        paths = ["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif"] + [
            f"extra{k}.png" for k in range(n_extra)
        ]
        assets = {}
        for path in paths:
            file = source_dir / path.replace("/", "_")
            file.write_bytes(f"sample-{index}:{path}:".encode() * (40 + index))
            assets[path] = file
        lon = -76.0 + index if lon is None else lon
        lat = -12.0 + index / 10 if lat is None else lat
        start = datetime(2024, 1, 1 + index, tzinfo=timezone.utc)
        return taco.Sample(
            assets=assets,
            metadata={
                "collection": {
                    "stac:crs": "EPSG:4326",
                    "stac:geometry": wkb_bbox(lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1),
                    "stac:centroid": wkb_point(lon, lat),
                    "stac:time_start": start,
                    "stac:time_end": datetime(2024, 1, 2 + index, tzinfo=timezone.utc),
                    "split": "train" if index % 2 == 0 else "val",
                    "cloud_cover": 10.5 * index,
                    "tags": ["a", "b"],
                },
                "sample": {
                    "before": {"kind": "imagery"},
                    "after": {"kind": "imagery"},
                    "mask.tif": {"kind": "label"},
                    **{f"extra{k}.png": {"kind": "extra"} for k in range(n_extra)},
                },
                "sample/before": {"B02.tif": {"resolution": 10}, "B03.tif": {"resolution": 10}},
                "sample/after": {"B02.tif": {"resolution": 20}},
            },
        )

    return factory


@pytest.fixture
def archive(tmp_path: Path, collection: taco.Collection, make_sample) -> Path:
    with taco.open_writer(collection, tmp_path / "dataset.zip") as writer:
        for index in range(4):
            writer.add(make_sample(index, index % 3))
        result = writer.run()
    return result.path


@pytest.fixture
def folder_dataset(tmp_path: Path, collection: taco.Collection, make_sample) -> Path:
    """The same samples as `archive`, written as a FOLDER dataset."""
    directory = tmp_path / "folder-dataset"
    with taco.open_folder(collection, directory) as writer:
        for index in range(4):
            writer.add(make_sample(index, index % 3))
        writer.run()
    return directory
