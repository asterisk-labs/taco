import io

import numpy as np

import taco


def encode(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


contract = taco.Contract(
    structure=["image*[2,5].npy"],
    metadata=taco.MetadataSchema(
        taco.Level("sample", ml=taco.metadata.sample.Split),
        taco.Level("children", raster=taco.metadata.asset.Raster),
    ),
)
collection = taco.Collection(
    contract=contract,
    id="image-sequence",
    dataset_version="1.0.0",
    description="Short image sequences with variable length",
    licenses=["MIT"],
    providers=["Asterisk Labs"],
    tasks=["classification"],
)

with taco.open_writer(collection, "image-sequence.zip", overwrite=True) as writer:
    for sample_index, length in enumerate((2, 3, 5)):
        assets = []
        for frame_index in range(length):
            image = np.full((8, 8), sample_index * 10 + frame_index, dtype=np.uint16)
            metadata = taco.Metadata(raster=taco.metadata.asset.Raster(resolution=10, num_bands=1, data_type="uint16"))
            assets.append(taco.Asset(encode(image), path=f"image{frame_index}.npy", metadata=metadata))
        split = "test" if sample_index == 2 else "train"
        writer.add(
            taco.Sample(
                assets=assets,
                metadata=taco.Metadata(ml=taco.metadata.sample.Split(split=split)),
            )
        )
    writer.run()

dataset = taco.open("image-sequence.zip")
assert dataset.read().num_rows == 3
assert taco.validate("image-sequence.zip").ok
