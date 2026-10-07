from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

import taco
from taco.ml import Dataset

rasterio = pytest.importorskip("rasterio")

CONTRACT = {
    "inputs": [{"name": "image", "kind": "raster", "modality": "optical", "path": "image.tif"}],
    "targets": [{"name": "label", "kind": "class_index", "field": "label", "classes": ["a", "b"]}],
    "tasks": ["scene-classification"],
}

_SAMPLE_IDS = itertools.count()


def _sample(**fields) -> taco.Sample:
    """A sample with a fresh id; the fixtures care about content, not identity."""
    return taco.Sample(id=f"s{next(_SAMPLE_IDS)}", **fields)



def _collection() -> taco.Collection:
    from pydantic import BaseModel

    class Label(BaseModel):
        label: int

    return taco.Collection(
        contract=taco.Contract(structure=["image.tif"],
                               metadata=[taco.Level("sample", ml=Label)]),
        id="ml-parts", description="Fixture for split collections",
        licenses=["CC-BY-4.0"], providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
        tasks=["scene-classification"],
        ml={"contract": CONTRACT},
    ), Label


def _write(path: Path, indices: range, tmp: Path) -> Path:
    collection, Label = _collection()
    samples = []
    for index in indices:
        tif = tmp / "src" / f"{index}.tif"
        tif.parent.mkdir(parents=True, exist_ok=True)
        # Filled with the GLOBAL index, so a row routed to the wrong part shows.
        # Georeferenced only so that reading it back does not warn; the grid itself
        # plays no part in the test.
        with rasterio.open(tif, "w", driver="GTiff", width=4, height=4, count=1,
                           dtype="uint8", crs="EPSG:4326",
                           transform=rasterio.transform.from_origin(0, 4, 1, 1)) as sink:
            sink.write(np.full((1, 4, 4), index, dtype="uint8"))
        samples.append(_sample(metadata=taco.Metadata(ml=Label(label=index % 2)),
                                   assets=[taco.Asset(tif, path="image.tif")]))
    with taco.open_writer(collection, path) as writer:
        writer.extend(samples)
        writer.run()
    return path


def test_parts_read_as_one_collection(tmp_path: Path) -> None:
    parts = [_write(tmp_path / "x.0000.zip", range(3), tmp_path),
             _write(tmp_path / "x.0001.zip", range(3, 5), tmp_path)]
    whole = Dataset(parts)
    assert len(whole) == 5
    for index in range(5):
        sample = whole[index]
        assert int(sample["image"].array[0, 0, 0]) == index
        assert sample["label"].array == index % 2
    # A single archive is still read on its own, with its own numbering.
    assert int(Dataset(parts[1])[0]["image"].array[0, 0, 0]) == 3


def test_parts_need_distinct_file_names(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.zip", range(1), tmp_path)
    other = tmp_path / "b"
    other.mkdir()
    second = _write(other / "a.zip", range(1, 2), tmp_path)
    with pytest.raises(ValueError, match="distinct file names"):
        Dataset([first, second])
