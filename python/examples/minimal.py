import taco

collection = taco.Collection(
    contract=taco.Contract(structure=None),
    id="minimal",
    dataset_version="1.0.0",
    description="Minimal TACO dataset",
    licenses=["MIT"],
    providers=["me"],
    tasks=["other"],
)
with taco.open_writer(collection, "minimal.zip", overwrite=True) as writer:
    writer.add(taco.Sample(assets=b"hello"))
    writer.run()

dataset = taco.open("minimal.zip")
assert dataset.read().num_rows == 1
