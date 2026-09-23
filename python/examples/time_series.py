import io
from datetime import datetime, timedelta, timezone
from typing import Annotated

import numpy as np
import pyarrow as pa
from pydantic import BaseModel

import taco


class Site(BaseModel):
    name: str


class Observation(BaseModel):
    time: Annotated[datetime, pa.timestamp("us", tz="UTC")]
    cloud_cover: float


def encode(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


contract = taco.Contract(
    structure=["image*[3,6].npy"],
    metadata=taco.MetadataSchema(
        taco.Level("sample", site=Site),
        taco.Level("children", observation=Observation),
    ),
)
collection = taco.Collection(
    contract=contract,
    id="time-series",
    description="Satellite observations collected through time",
    licenses=["MIT"],
    providers=["Asterisk Labs"],
    tasks=["classification"],
)

start = datetime(2024, 1, 1, tzinfo=timezone.utc)
with taco.open_writer(collection, "time-series.zip", overwrite=True) as writer:
    for site_index, length in enumerate((3, 5)):
        assets = []
        for step in range(length):
            image = np.full((4, 16, 16), site_index * 100 + step, dtype=np.uint16)
            metadata = taco.Metadata(
                observation=Observation(
                    time=start + timedelta(days=step * 5),
                    cloud_cover=float(step * 8),
                ),
            )
            assets.append(taco.Asset(encode(image), path=f"image{step}.npy", metadata=metadata))
        writer.add(
            taco.Sample(
                id=f"site-{site_index}",
                assets=assets,
                metadata=taco.Metadata(site=Site(name=f"site-{site_index}")),
            )
        )
    writer.run()

dataset = taco.open_dataset("time-series.zip")
table = taco.read(dataset)
lengths = dict(
    zip(table.column("sample_id").to_pylist(), map(len, table.column("image::location").to_pylist()), strict=True)
)
assert lengths == {0: 3, 1: 5}
assert dataset.sql("SELECT * FROM files").num_rows == 8
assert taco.validate("time-series.zip").ok
