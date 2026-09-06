from pydantic import BaseModel

import taco


class ML(BaseModel):
    split: str


contract = taco.Contract(
    structure=["image.bin", "label.bin"],
    metadata=taco.MetadataSchema(taco.Level("sample", ml=ML)),
)
collection = taco.Collection(
    contract=contract,
    id="demo",
    dataset_version="1.0.0",
    description="Minimal end-to-end dataset",
    licenses=["MIT"],
    providers=["me"],
    tasks=["segmentation"],
)
sample = taco.Sample(
    metadata=taco.Metadata(ml=ML(split="train")),
    assets=[
        taco.Asset(b"image", path="image.bin"),
        taco.Asset(b"label", path="label.bin"),
    ],
)
with taco.open_writer(collection, "demo.zip", overwrite=True) as writer:
    writer.add(sample)
    writer.run()

assert taco.validate("demo.zip").ok
