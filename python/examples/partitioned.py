import taco

contract = taco.Contract(
    structure=None,
    metadata=taco.MetadataSchema(taco.Level("sample", ml=taco.metadata.sample.Split)),
)
collection = taco.Collection(
    contract=contract,
    id="partitioned",
    dataset_version="1.0.0",
    description="Dataset partitioned by training split",
    licenses=["MIT"],
    providers=["Asterisk Labs"],
    tasks=["classification"],
)

with taco.open_writer(
    collection,
    "partitioned.zip",
    partition_by="ml:split",
    overwrite=True,
) as writer:
    for index, split in enumerate(("train", "train", "validation", "test")):
        writer.add(
            taco.Sample(
                assets=f"sample-{index}".encode(),
                metadata=taco.Metadata(ml=taco.metadata.sample.Split(split=split)),
            )
        )
    catalog = writer.run().path

dataset = taco.open(catalog)
assert dataset.read().num_rows == 4
assert taco.validate(catalog).ok
