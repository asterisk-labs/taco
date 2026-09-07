import struct
from datetime import datetime, timezone

import taco


def point(longitude: float, latitude: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, longitude, latitude)


contract = taco.Contract(
    structure=["image.bin"],
    metadata=taco.MetadataSchema(
        taco.Level(
            "sample",
            stac=taco.metadata.sample.STAC,
            majortom=taco.metadata.sample.MajorTOM(
                dist_km=100,
                latitude_range=(-20, 0),
                longitude_range=(-90, -60),
            ),
        ),
        taco.Level("children", raster=taco.metadata.asset.Raster),
    ),
)
collection = taco.Collection(
    contract=contract,
    id="peru-sites",
    dataset_version="1.0.0",
    description="Small geospatial dataset with derived MajorTOM cells",
    licenses=["MIT"],
    providers=["Asterisk Labs"],
    tasks=["classification"],
)

sites = [(-77.04, -12.05), (-71.97, -13.53), (-80.63, -5.19)]
with taco.open_writer(collection, "geospatial.zip", overwrite=True) as writer:
    for index, (longitude, latitude) in enumerate(sites):
        location = point(longitude, latitude)
        stac = taco.metadata.sample.STAC(
            crs="EPSG:4326",
            geometry=location,
            centroid=location,
            time_start=datetime(2024, 1, index + 1, tzinfo=timezone.utc),
        )
        asset = taco.Asset(
            bytes([index + 1]) * 64,
            path="image.bin",
            metadata=taco.Metadata(raster=taco.metadata.asset.Raster(resolution=10, num_bands=1, data_type="uint8")),
        )
        writer.add(taco.Sample(assets=asset, metadata=taco.Metadata(stac=stac)))
    writer.run()

dataset = taco.open("geospatial.zip")
table = dataset.read()
assert table.num_rows == 3
assert "majortom:code" in table.column_names
assert dataset.collection.extent is not None
assert dataset.collection.extent.spatial == (-80.63, -13.53, -71.97, -5.19)
assert taco.validate("geospatial.zip").ok
