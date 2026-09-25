from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import shapely
from shapely.geometry import Point, Polygon

import taco
from taco.container.view import open_view
from taco.metadata.spatiotemporal import (
    footprint_bbox,
    footprint_center,
    grid_center,
    grid_footprint,
    point_from_wkb,
    point_wkb,
)

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 3, tzinfo=timezone.utc)
# A 10 x 10 grid of 0.25 degree pixels from (-1.25, 1.25) to (1.25, -1.25).
GRID = {"proj_code": "EPSG:4326", "proj_shape": (10, 10), "proj_transform": (0.25, 0, -1.25, 0, -0.25, 1.25)}


def collection(contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id="spatiotemporal-extensions",
        description="Spatiotemporal extension tests",
        licenses=["MIT"],
        providers=["TACO tests"],
        tasks=["other"],
    )


def write(tmp_path: Path, contract: taco.Contract, *samples: taco.Sample) -> Path:
    output = tmp_path / "dataset"
    with taco.open_writer(collection(contract), output) as writer:
        writer.extend(samples)
        writer.run()
    return output


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
    stac = taco.metadata.sample.STAC(**GRID, start_datetime=START, end_datetime=END)
    output = write(tmp_path, contract, taco.Sample(id="u15", assets=b"x", metadata=taco.Metadata(stac=stac)))

    row = open_view(output).level("sample").to_pylist()[0]
    assert shapely.from_wkb(row["stac:geometry"]).equals(shapely.box(-1.25, -1.25, 1.25, 1.25))
    assert row["stac:bbox"] == [-1.25, -1.25, 1.25, 1.25]
    assert row["stac:datetime"] is None
    assert row["stac:start_datetime"] == START
    assert row["majortom:code"].startswith("MT10km_")
    assert taco.validate(output).ok


def test_spatial_composes_with_majortom_and_summarizes_boxes(tmp_path: Path) -> None:
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
    spatial = taco.metadata.sample.Spatial(**GRID)
    output = write(tmp_path, contract, taco.Sample(id="u16", assets=b"x", metadata=taco.Metadata(spatial=spatial)))

    dataset = open_view(output)
    row = dataset.level("sample").to_pylist()[0]
    assert row["spatial:bbox"] == [-1.25, -1.25, 1.25, 1.25]
    assert row["spatial:centroid"] == point_wkb(0, 0)
    assert row["majortom:code"].startswith("MT100km_")
    assert not any(name.startswith(("stac:", "temporal:")) for name in row)
    assert dataset.collection.extent == taco.contract.Extent((-1.25, -1.25, 1.25, 1.25))


def test_temporal_is_a_plain_profile(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", temporal=taco.metadata.sample.Temporal)],
    )
    temporal = taco.metadata.sample.Temporal(start_datetime=START, end_datetime=END)
    output = write(tmp_path, contract, taco.Sample(id="u17", assets=b"x", metadata=taco.Metadata(temporal=temporal)))

    dataset = open_view(output)
    row = dataset.level("sample").to_pylist()[0]
    assert (row["temporal:start_datetime"], row["temporal:end_datetime"]) == (START, END)
    assert row["temporal:datetime"] is None
    assert not any(name.startswith(("spatial:", "stac:")) for name in row)
    assert dataset.collection.extent is None
    assert taco.validate(output).ok


def test_stac_keeps_a_supplied_footprint(tmp_path: Path) -> None:
    footprint = Polygon([(-77, -13), (-75, -13), (-76, -11)])
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.STAC())],
    )
    stac = taco.metadata.sample.STAC(geometry=footprint.wkb, datetime=START)
    output = write(tmp_path, contract, taco.Sample(id="u18", assets=b"x", metadata=taco.Metadata(stac=stac)))

    dataset = open_view(output)
    row = dataset.level("sample").to_pylist()[0]
    assert row["stac:geometry"] == footprint.wkb
    assert row["stac:bbox"] == [-77, -13, -75, -11]
    assert row["stac:proj_code"] is None
    assert dataset.collection.extent == taco.contract.Extent(
        (-77, -13, -75, -11),
        ("2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
    )


def test_stac_extension_runs_on_folder_metadata(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["scene/dem.bin"],
        metadata=[taco.Level("children", stac=taco.extensions.STAC(model=taco.metadata.folder.STAC))],
    )
    stac = taco.metadata.folder.STAC(**GRID, datetime=START)
    sample = taco.Sample(
        id="s0",
        folders=[taco.Folder("scene", metadata=taco.Metadata(stac=stac))],
        assets=[taco.Asset(b"x", path="scene/dem.bin")],
    )
    output = write(tmp_path, contract, sample)

    row = open_view(output).level("children").to_pylist()[0]
    assert row["stac:bbox"] == [-1.25, -1.25, 1.25, 1.25]


def test_grid_footprint_uses_the_complete_affine_transform() -> None:
    # x = 2*column + 3*row + 10 and y = 4*column + 5*row + 20 on a grid of 2 rows and 4 columns.
    footprint = shapely.from_wkb(grid_footprint("EPSG:4326", (2, 4), (2, 3, 10, 4, 5, 20)))
    assert footprint.equals(Polygon([(10, 20), (18, 36), (24, 46), (16, 30)]))
    assert shapely.is_ccw(footprint.exterior)


def test_grid_footprint_reprojects_to_wgs84() -> None:
    footprint = shapely.from_wkb(grid_footprint("EPSG:3857", (20, 20), (1, 0, -10, 0, -1, 10)))
    west, south, east, north = footprint_bbox(footprint)
    assert west == pytest.approx(-east)
    assert south == pytest.approx(-north)
    assert east == pytest.approx(10 / 6378137 * 180 / 3.141592653589793)


def test_grid_footprint_is_split_at_the_antimeridian() -> None:
    wkb = grid_footprint("EPSG:4326", (10, 10), (0.1, 0, 179.5, 0, -0.1, 1))
    footprint = shapely.from_wkb(wkb)
    assert footprint.geom_type == "MultiPolygon"
    assert footprint_bbox(footprint) == pytest.approx((179.5, 0, -179.5, 1))
    assert footprint_center(wkb) == pytest.approx((180, 0.5))


def test_grid_footprint_around_a_pole_is_a_cap() -> None:
    wkb = grid_footprint("EPSG:3413", (200, 200), (10_000, 0, -1_000_000, 0, -10_000, 1_000_000))
    west, south, east, north = footprint_bbox(shapely.from_wkb(wkb))
    assert (west, east, north) == (-180, 180, 90)
    assert 76 < south < 78


def test_unknown_crs_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a known CRS"):
        grid_footprint("EPSG:99999", (2, 2), (1, 0, 0, 0, -1, 0))


def test_footprint_must_be_split_at_the_antimeridian() -> None:
    wrapped = Polygon([(179, 0), (-179, 0), (-179, 1), (179, 1)])
    with pytest.raises(ValueError, match="crosses the antimeridian"):
        taco.metadata.sample.STAC(geometry=wrapped.wkb, datetime=START)


def test_centroid_is_the_exact_grid_center() -> None:
    from pyproj import Transformer

    # A 264-pixel UTM chip: the center is reprojected alone, not taken from the footprint.
    center = point_from_wkb(grid_center("EPSG:32718", (264, 264), (10, 0, 277000, 0, -10, 8667000)))
    expected = Transformer.from_crs("EPSG:32718", "EPSG:4326", always_xy=True).transform(278320, 8665680)
    assert center == expected


def test_centroid_without_a_grid_comes_from_the_footprint(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.STAC(), majortom=taco.extensions.MajorTOM())],
    )
    split = shapely.MultiPolygon([shapely.box(179.5, 0, 180, 1), shapely.box(-180, 0, -179.5, 1)])
    stac = taco.metadata.sample.STAC(geometry=split.wkb, datetime=START)
    output = write(tmp_path, contract, taco.Sample(id="u19", assets=b"x", metadata=taco.Metadata(stac=stac)))

    row = open_view(output).level("sample").to_pylist()[0]
    # The two halves are joined across the antimeridian before the centroid is taken.
    assert point_from_wkb(row["stac:centroid"]) == pytest.approx((180, 0.5))
    assert (
        row["majortom:code"] == taco.extensions.MajorTOM().compute({"stac:centroid": [Point(180, 0.5).wkb]})["code"][0]
    )


def test_supplied_centroid_is_kept(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", stac=taco.extensions.STAC())],
    )
    stac = taco.metadata.sample.STAC(**GRID, centroid=point_wkb(1, 1), datetime=START)
    output = write(tmp_path, contract, taco.Sample(id="u20", assets=b"x", metadata=taco.Metadata(stac=stac)))
    assert open_view(output).level("sample").to_pylist()[0]["stac:centroid"] == point_wkb(1, 1)


def test_projected_footprint_retains_curved_edges() -> None:
    from pyproj import Transformer

    footprint = shapely.from_wkb(grid_footprint("EPSG:32631", (100, 100), (10_000, 0, 0, 0, -10_000, 8_000_000)))
    # The top edge reaches farther north at its midpoint than at either corner.
    midpoint = Point(Transformer.from_crs(32631, 4326, always_xy=True).transform(500_000, 8_000_000))
    assert footprint.distance(midpoint) < 1e-9
    assert footprint.bounds[3] == pytest.approx(midpoint.y)


@pytest.mark.parametrize(("code", "latitude"), [("EPSG:3413", 78), ("EPSG:3031", -78)])
def test_polar_footprint_does_not_fill_its_bounding_box(code: str, latitude: float) -> None:
    footprint = shapely.from_wkb(grid_footprint(code, (200, 200), (10_000, 0, -1_000_000, 0, -10_000, 1_000_000)))
    assert footprint.is_valid
    assert footprint.area < shapely.box(*footprint.bounds).area
    assert not footprint.covers(Point(0 if latitude < 0 else -45, latitude))


def test_global_grid_retains_all_longitudes() -> None:
    footprint = shapely.from_wkb(grid_footprint("EPSG:4326", (180, 360), (1, 0, -180, 0, -1, 90)))
    assert footprint.equals(shapely.box(-180, -90, 180, 90))


def test_self_intersecting_footprint_is_rejected() -> None:
    footprint = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    with pytest.raises(ValueError, match="not a valid geometry"):
        taco.metadata.sample.STAC(geometry=footprint.wkb, datetime=START)
