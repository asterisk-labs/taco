import io

import numpy as np
from pydantic import BaseModel

import taco


class ML(BaseModel):
    split: str


def encode(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


contract = taco.Contract(
    structure=["image.npy", "mask.npy"],
    metadata=taco.MetadataSchema(taco.Level("sample", ml=ML)),
)
collection = taco.Collection(
    contract=contract,
    id="numpy-demo",
    dataset_version="1.0.0",
    description="Small NumPy dataset",
    licenses=["MIT"],
    providers=["me"],
    tasks=["segmentation"],
)
with taco.open_writer(collection, "numpy-demo.zip", overwrite=True) as writer:
    for index in range(10):
        image = np.random.default_rng(index).random((3, 32, 32), dtype=np.float32)
        writer.add(
            taco.Sample(
                metadata=taco.Metadata(ml=ML(split="train" if index < 8 else "test")),
                assets=[
                    taco.Asset(encode(image), path="image.npy"),
                    taco.Asset(encode(image[0] > 0.5), path="mask.npy"),
                ],
            )
        )
    writer.run()

dataset = taco.open_dataset("numpy-demo.zip")
assert taco.read(dataset).num_rows == 10
assert taco.validate("numpy-demo.zip").ok
