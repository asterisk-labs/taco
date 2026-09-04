from __future__ import annotations

import io
import tempfile
from pathlib import Path

import numpy as np

from taco import Collection, Contract, Sample, open_writer, validate
from taco.reader import read

# taco.reader loads the cozip DuckDB extension. While it is unpublished, set
# COZIP_EXTENSION to a local build of the sibling cozip_reader repository.

# 1. The contract: what every sample holds, and what metadata it carries.
contract = Contract(
    structure=["image.npy", "mask.npy"],
    metadata={
        "collection": {
            "split": ["string", "Dataset split (train/val)"],
            "cloud_cover": ["double", "Cloud cover percentage"],
        }
    },
)

collection = Collection(
    contract=contract,
    id="numpy-demo",
    dataset_version="1.0.0",
    description="Tiny synthetic dataset stored as .npy files",
    licenses=["CC-BY-4.0"],
    providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
    tasks=["segmentation"],
)


def npy(array: np.ndarray) -> bytes:
    """Serialize an array to .npy bytes, so no temporary file is needed."""
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


# 2. Write. add() only validates and stages; run() publishes once.
rng = np.random.default_rng(0)
output = Path(tempfile.gettempdir()) / "numpy_demo.zip"

with open_writer(collection, output, overwrite=True) as writer:
    for index in range(10):
        image = rng.random((3, 32, 32), dtype=np.float32)
        mask = (image[0] > 0.5).astype(np.uint8)
        writer.add(
            Sample(
                assets={"image.npy": npy(image), "mask.npy": npy(mask)},
                metadata={
                    "collection": {
                        "split": "train" if index < 8 else "val",
                        "cloud_cover": float(rng.uniform(0, 100)),
                    }
                },
            )
        )
    result = writer.run()

print(f"wrote {result.path} ({result.samples} samples, {result.data_files} files, {result.size} bytes)")
print(validate(result.path))

# 3. Query. One row per sample, one column per file of the contract.
table = read(result.path)
print(table.select(["sample_id", "split", "cloud_cover"]).to_pandas().head(3))

# 4. Read one array back through the byte range the reader handed us.
path = read(result.path, idx=3).column("image.npy").to_pylist()[0]
offset, size = (int(part) for part in path.removeprefix("/vsisubfile/").split(",")[0].split("_"))
with result.path.open("rb") as archive:
    archive.seek(offset)
    array = np.load(io.BytesIO(archive.read(size)))
print(f"sample 3 image.npy -> shape={array.shape} dtype={array.dtype} via {path}")
