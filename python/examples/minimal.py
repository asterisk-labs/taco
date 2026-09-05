from pathlib import Path

from taco import Collection, Contract, Sample, open_writer, validate
from taco.reader import read

collection = Collection(
    contract=Contract(structure=None),
    id="minimal",
    dataset_version="1.0.0",
    description="Minimal TACO dataset",
    licenses=["MIT"],
    providers=["me"],
    tasks=["other"],
)
with open_writer(collection, Path("minimal.zip"), overwrite=True) as writer:
    writer.add(Sample(assets=b"hello"))
    archive = writer.run().path

print(validate(archive))
print(read(archive))
