import io

import numpy as np
from pydantic import BaseModel

import taco


class Acquisition(BaseModel):
    year: int


def encode(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


raster = taco.metadata.asset.Raster
contract = taco.Contract(
    structure=[
        "before/B02.npy",
        "before/B03.npy",
        "after/B02.npy",
        "after/B03.npy",
        "change.npy",
    ],
    metadata=taco.MetadataSchema(
        taco.Level("sample", ml=taco.metadata.sample.Split),
        taco.Level("children", acquisition=Acquisition | None, raster=raster | None),
        taco.Level("children/before", raster=raster),
        taco.Level("children/after", raster=raster),
    ),
)
collection = taco.Collection(
    contract=contract,
    id="change-detection",
    dataset_version="1.0.0",
    description="Paired observations and their change mask",
    licenses=["MIT"],
    providers=["Asterisk Labs"],
    tasks=["change-detection"],
)

before = np.arange(2 * 16 * 16, dtype=np.uint16).reshape(2, 16, 16)
after = before.copy()
after[:, 5:11, 7:13] += 100
paths = {
    "before/B02.npy": before[0],
    "before/B03.npy": before[1],
    "after/B02.npy": after[0],
    "after/B03.npy": after[1],
    "change.npy": np.any(before != after, axis=0),
}
assets = [
    taco.Asset(
        encode(array),
        path=path,
        metadata=taco.Metadata(raster=raster(resolution=10, num_bands=1, data_type=str(array.dtype))),
    )
    for path, array in paths.items()
]
sample = taco.Sample(
    assets=assets,
    metadata=taco.Metadata(ml=taco.metadata.sample.Split(split="train")),
    folders=[
        taco.Folder("before", metadata=taco.Metadata(acquisition=Acquisition(year=2020))),
        taco.Folder("after", metadata=taco.Metadata(acquisition=Acquisition(year=2024))),
    ],
)

with taco.open_writer(collection, "change-detection", overwrite=True) as writer:
    writer.add(sample)
    writer.run()

dataset = taco.open("change-detection")
assert dataset.read().num_rows == 1
assert taco.validate("change-detection").ok
