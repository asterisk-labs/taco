from datetime import datetime, timezone

import taco

contract = taco.Contract(
    structure=["image.bin"],
    metadata=[
        taco.Level(
            "sample",
            stac=taco.extensions.STAC(),
            majortom=taco.extensions.MajorTOM(
                dist_km=100,
                latitude_range=(-20, 0),
                longitude_range=(-90, -60),
            ),
        ),
    ],
)
collection = taco.Collection(
    contract=contract,
    id="peru-sites",
    description="Small geospatial dataset with derived MajorTOM cells",
    licenses=["MIT"],
    providers=["Asterisk Labs"],
    tasks=["classification"],
)

sites = [(-77.04, -12.05), (-71.97, -13.53), (-80.63, -5.19)]
with taco.open_writer(collection, "geospatial.zip", overwrite=True) as writer:
    for index, (longitude, latitude) in enumerate(sites):
        stac = taco.metadata.sample.STAC(
            crs="EPSG:4326",
            tensor_shape=(1, 8, 8),
            geotransform=(longitude - 0.05, 0.0125, 0, latitude + 0.05, 0, -0.0125),
            time_start=datetime(2024, 1, index + 1, tzinfo=timezone.utc),
        )
        asset = taco.Asset(bytes([index + 1]) * 64, path="image.bin")
        writer.add(taco.Sample(id=f"scene-{index}", assets=asset, metadata=taco.Metadata(stac=stac)))
    writer.run()

dataset = taco.open_dataset("geospatial.zip")
table = taco.read(dataset)
assert table.num_rows == 3
assert "majortom:code" in table.column_names
assert dataset.collection.extent is not None
assert dataset.collection.extent.spatial == (-80.63, -13.53, -71.97, -5.19)
assert taco.validate("geospatial.zip").ok
