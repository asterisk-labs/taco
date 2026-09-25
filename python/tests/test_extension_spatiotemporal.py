from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

import pytest
from shapely.geometry import Polygon

import taco
from taco.container.view import open_view


def point(longitude: float, latitude: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, longitude, latitude)


def coordinates(value: bytes) -> tuple[float, float]:
    _, geometry_type, longitude, latitude = struct.unpack("<BIdd", value)
    assert geometry_type == 1
    return longitude, latitude


def collection(contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id="spatiotemporal-extensions",
        description="Spatiotemporal extension tests",
        licenses=["MIT"],
        providers=["TACO tests"],
        tasks=["other"],
    )


def stac(model: type[taco.metadata.sample.STAC] = taco.metadata.sample.STAC) -> taco.metadata.sample.STAC:
    return model(
        crs="EPSG:4326",
        tensor_shape=(1, 10, 10),
        geotransform=(-1, 0.2, 0, 1, 0, -0.2),
        time_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        time_end=datetime(2024, 1, 3, tzinfo=timezone.utc),
    )


def spatial(
    model: type[taco.metadata.sample.Spatial] = taco.metadata.sample.Spatial,
) -> taco.metadata.sample.Spatial:
    return model(
        crs="EPSG:4326",
        tensor_shape=(1, 10, 10),
        geotransform=(-1, 0.2, 0, 1, 0, -0.2),
    )


def test_extension_dependencies_ignore_declaration_order(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[
            taco.Level(
                "sample",
                majortom=taco.extensions.MajorTOM(dist_km=10),
                stac=taco.extensions.STAC(),
            )
        ],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u15", assets=b"x", metadata=taco.Metadata(stac=stac())))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["stac:centroid"] == point(0, 0)
    assert row["stac:time_middle"] == datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert row["majortom:code"].startswith("MT10km_")


def test_spatial_is_regular_only_and_composes_with_majortom(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[
            taco.Level(
                "sample",
                majortom=taco.extensions.MajorTOM(centroid="spatial:centroid"),
                spatial=taco.extensions.Spatial(),
            )
        ],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u16", assets=b"x", metadata=taco.Metadata(spatial=spatial())))
        writer.run()

    dataset = open_view(tmp_path / "dataset")
    row = dataset.level("sample").to_pylist()[0]
    assert row["spatial:centroid"] == point(0, 0)
    assert row["majortom:code"].startswith("MT100km_")
    assert not any(name.startswith(("stac:", "temporal:")) for name in row)
    assert dataset.collection.extent == taco.contract.Extent((0, 0, 0, 0))


def test_temporal_is_temporal_only(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", temporal=taco.extensions.Temporal())],
    )
    metadata = taco.metadata.sample.Temporal(
        time_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        time_end=datetime(2024, 1, 3, tzinfo=timezone.utc),
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u17", assets=b"x", metadata=taco.Metadata(temporal=metadata)))
        writer.run()

    dataset = open_view(tmp_path / "dataset")
    row = dataset.level("sample").to_pylist()[0]
    assert row["temporal:time_middle"] == datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert not any(name.startswith(("spatial:", "stac:", "istac:", "ispatial:")) for name in row)
    assert dataset.collection.extent is None


def test_ispatial_is_irregular_spatial_only(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", ispatial=taco.extensions.ISpatial())],
    )
    metadata = taco.metadata.sample.ISpatial(
        crs="EPSG:4326",
        geometry=Polygon([(-77, -13), (-75, -13), (-75, -11), (-77, -11)]).wkb,
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u18", assets=b"x", metadata=taco.Metadata(ispatial=metadata)))
        writer.run()

    dataset = open_view(tmp_path / "dataset")
    row = dataset.level("sample").to_pylist()[0]
    assert coordinates(row["ispatial:centroid"]) == pytest.approx((-76, -12))
    assert dataset.collection.extent == taco.contract.Extent((-76, -12, -76, -12))


def test_stac_centroid_uses_the_complete_affine_transform() -> None:
    # Center pixel coordinates are (column=2, row=1). Rotation contributes to
    # both output axes: x = 10 + 2*2 + 1*3, y = 20 + 2*4 + 1*5.
    assert taco.extensions.spatiotemporal.raster_centroid("EPSG:4326", (10, 2, 3, 20, 4, 5), (1, 2, 4)) == point(17, 33)


def test_stac_centroid_reprojects_to_wgs84() -> None:
    assert taco.extensions.spatiotemporal.raster_centroid("EPSG:3857", (-10, 1, 0, 10, 0, -1), (1, 20, 20)) == point(
        0, 0
    )


def test_stac_preserves_explicit_centroid_and_midpoint(tmp_path: Path) -> None:
    metadata = stac().model_copy(
        update={
            "centroid": point(-76, -12),
            "time_middle": datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
        }
    )
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.STAC())],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u19", assets=b"x", metadata=taco.Metadata(stac=metadata)))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["stac:centroid"] == point(-76, -12)
    assert row["stac:time_middle"] == datetime(2024, 1, 1, 12, tzinfo=timezone.utc)


def test_stac_extension_runs_on_folder_metadata(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["scene/dem.bin"],
        metadata=[
            taco.Level("children", stac=taco.extensions.STAC(model=taco.metadata.folder.STAC))
        ],
    )
    sample = taco.Sample(
        id="s0",
        folders=[taco.Folder("scene", metadata=taco.Metadata(stac=stac(taco.metadata.folder.STAC)))],
        assets=[taco.Asset(b"x", path="scene/dem.bin")],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(sample)
        writer.run()
    row = open_view(tmp_path / "dataset").level("children").to_pylist()[0]
    assert row["stac:centroid"] == point(0, 0)


def test_istac_centroid_is_derived_from_geometry(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", istac=taco.extensions.ISTAC())],
    )
    metadata = taco.metadata.sample.ISTAC(
        crs="EPSG:4326",
        geometry=Polygon([(-77, -13), (-75, -13), (-75, -11), (-77, -11)]).wkb,
        time_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u20", assets=b"x", metadata=taco.Metadata(istac=metadata)))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert coordinates(row["istac:centroid"]) == pytest.approx((-76, -12))


def test_istac_extension_runs_on_folder_metadata(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["scene/dem.bin"],
        metadata=[
            taco.Level("children", istac=taco.extensions.ISTAC(model=taco.metadata.folder.ISTAC))
        ],
    )
    metadata = taco.metadata.folder.ISTAC(
        crs="EPSG:4326",
        geometry=Polygon([(-77, -13), (-75, -13), (-75, -11), (-77, -11)]).wkb,
        time_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    sample = taco.Sample(
        id="s1",
        folders=[taco.Folder("scene", metadata=taco.Metadata(istac=metadata))],
        assets=[taco.Asset(b"x", path="scene/dem.bin")],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(sample)
        writer.run()
    row = open_view(tmp_path / "dataset").level("children").to_pylist()[0]
    assert coordinates(row["istac:centroid"]) == pytest.approx((-76, -12))


@pytest.mark.integration
def test_istac_antimeridian_centroid_uses_the_short_side() -> None:
    geometry = Polygon([(179, -1), (-179, -1), (-179, 1), (179, 1)]).wkb
    longitude, latitude = coordinates(
        taco.extensions.spatiotemporal.geometry_centroid(
            "EPSG:4326",
            geometry,
            check_antimeridian=True,
        )
    )
    assert abs(longitude) == pytest.approx(180)
    assert latitude == pytest.approx(0)
