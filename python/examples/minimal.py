import taco

collection = taco.Collection(
    contract=taco.Contract(structure=["data.bin"]),
    id="minimal",
    description="Minimal TACO dataset",
    licenses=["MIT"],
    providers=["me"],
    tasks=["other"],
)
with taco.open_writer(collection, "minimal.zip", overwrite=True) as writer:
    writer.add(taco.Sample(id="hello", assets=b"hello"))
    writer.run()

dataset = taco.open_dataset("minimal.zip")
assert taco.read(dataset).num_rows == 1
