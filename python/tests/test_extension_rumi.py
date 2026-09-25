from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import taco
from taco.container.view import open_view


def collection(contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id="rumi-extension",
        description="Rumi extension tests",
        licenses=["MIT"],
        providers=["TACO tests"],
        tasks=["other"],
    )


def fake_rumi(monkeypatch: pytest.MonkeyPatch, array: np.ndarray, *, header: bytes = b"canonical-header") -> None:
    fake = SimpleNamespace(
        info=lambda *, source: SimpleNamespace(header=header),
        read=lambda source, actual_header: array,
    )
    monkeypatch.setitem(sys.modules, "rumi", fake)


def test_rumi_stats_contract_matches_the_spec() -> None:
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=taco.MetadataSchema(taco.Level("sample", rumi=taco.extensions.Rumi(stats=True))),
    )
    assert contract.metadata["sample"]["rumi:stats"].type == (
        "list<struct<minimum: double?, maximum: double?, mean: double?, stddev: double?, "
        "valid_count: int64, nodata_count: int64>>"
    )


def write_fake_rumi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    array: np.ndarray,
    *,
    stats: bool = True,
    nodata: float | int | None = None,
) -> dict[str, object]:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake_rumi(monkeypatch, array)
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=taco.MetadataSchema(taco.Level("sample", rumi=taco.extensions.Rumi(stats=stats, nodata=nodata))),
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u10", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    return open_view(tmp_path / "dataset").level("sample").to_pylist()[0]


def test_rumi_extension_writes_binary_header_and_named_band_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    array = np.array([[[1, 2], [3, -999]], [[5, 5], [5, 5]]], dtype=np.int16)
    row = write_fake_rumi(tmp_path, monkeypatch, array, nodata=-999)
    assert row["rumi:header"] == b"canonical-header"
    assert row["rumi:stats"] == [
        {
            "minimum": 1.0,
            "maximum": 3.0,
            "mean": 2.0,
            "stddev": pytest.approx(0.816496580927726),
            "valid_count": 3,
            "nodata_count": 1,
        },
        {
            "minimum": 5.0,
            "maximum": 5.0,
            "mean": 5.0,
            "stddev": 0.0,
            "valid_count": 4,
            "nodata_count": 0,
        },
    ]


def test_rumi_header_only_does_not_decode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake = SimpleNamespace(
        info=lambda *, source: SimpleNamespace(header=b"header-only"),
        read=lambda *args, **kwargs: pytest.fail("stats=False must not decode the asset"),
    )
    monkeypatch.setitem(sys.modules, "rumi", fake)
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=taco.MetadataSchema(taco.Level("sample", rumi=taco.extensions.Rumi())),
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u11", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["rumi:header"] == b"header-only"
    assert "rumi:stats" not in row


def test_rumi_cube_stats_combine_time_and_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    array = np.array(
        [
            [[[1, np.nan]], [[5, -999]]],
            [[[3, np.inf]], [[7, -999]]],
        ],
        dtype=np.float32,
    )
    stats = write_fake_rumi(tmp_path, monkeypatch, array, nodata=-999)["rumi:stats"]
    assert stats == [
        {
            "minimum": 1.0,
            "maximum": 3.0,
            "mean": 2.0,
            "stddev": 1.0,
            "valid_count": 2,
            "nodata_count": 2,
        },
        {
            "minimum": 5.0,
            "maximum": 7.0,
            "mean": 6.0,
            "stddev": 1.0,
            "valid_count": 2,
            "nodata_count": 2,
        },
    ]


def test_rumi_all_missing_band_has_nullable_statistics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    array = np.full((1, 2, 2), np.nan, dtype=np.float32)
    stats = write_fake_rumi(tmp_path, monkeypatch, array)["rumi:stats"]
    assert stats == [
        {
            "minimum": None,
            "maximum": None,
            "mean": None,
            "stddev": None,
            "valid_count": 0,
            "nodata_count": 4,
        }
    ]


def test_rumi_extension_can_store_stats_without_the_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake_rumi(monkeypatch, np.ones((1, 2, 2), dtype=np.int16))
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=taco.MetadataSchema(taco.Level("sample", rumi=taco.extensions.Rumi(header=False, stats=True))),
    )
    assert "rumi:header" not in contract.metadata["sample"]
    output = tmp_path / "dataset.zip"
    with taco.open_writer(collection(contract), output) as writer:
        writer.add(taco.Sample(id="u15", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    row = taco.read(output).to_pylist()[0]
    assert row["rumi:stats"][0]["valid_count"] == 4
    assert "data.rumi::header" not in row


def test_rumi_extension_rejects_an_empty_configuration() -> None:
    with pytest.raises(ValueError, match="header=True or stats=True"):
        taco.extensions.Rumi(header=False)


def test_rumi_extension_runs_at_asset_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake_rumi(monkeypatch, np.ones((1, 2, 2), dtype=np.int16), header=b"asset-header")
    contract = taco.Contract(
        structure=["dem.rumi"],
        metadata=taco.MetadataSchema(taco.Level("children", rumi=taco.extensions.Rumi())),
    )
    sample = taco.Sample(id="u12", assets=taco.Asset(source, path="dem.rumi"))
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(sample)
        writer.run()
    row = open_view(tmp_path / "dataset").level("children").to_pylist()[0]
    assert row["rumi:header"] == b"asset-header"


def test_rumi_extension_rejects_a_non_rumi_asset_without_importing_rumi(tmp_path: Path) -> None:
    source = tmp_path / "dem.tif"
    source.write_bytes(b"not rumi")
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=taco.MetadataSchema(taco.Level("sample", rumi=taco.extensions.Rumi())),
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u13", assets=taco.Asset(source, path="data.rumi")))
        with pytest.raises(ValueError, match=r"\.rumi"):
            writer.run()


@pytest.mark.integration
def test_rumi_extension_reads_a_real_rumi_file(tmp_path: Path) -> None:
    import geozl
    import rumi

    source = tmp_path / "dem.rumi"
    image = np.arange(16, dtype=np.int16).reshape(1, 4, 4)
    frames = rumi.frames(image, "b (row h) (col w) -> row col b (h w)", tile_size=2)
    for frame in frames:
        frame.compressed = geozl.compress(frame.data, graph=geozl.graph(frame.data, "id>zstd"))
    _, expected_header = rumi.write(source, frames)

    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=taco.MetadataSchema(taco.Level("sample", rumi=taco.extensions.Rumi(stats=True))),
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u14", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["rumi:header"] == expected_header
    assert row["rumi:stats"][0]["valid_count"] == 16
    assert row["rumi:stats"][0]["mean"] == 7.5


def test_wide_reads_carry_rumi_headers_next_to_locations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    fake = SimpleNamespace(info=lambda *, source: SimpleNamespace(header=Path(source).name.encode()))
    monkeypatch.setitem(sys.modules, "rumi", fake)
    for name in ("optical.rumi", "img0.rumi", "img1.rumi", "mask.bin"):
        (tmp_path / name).write_bytes(name.encode())
    contract = taco.Contract(
        structure=["scene/optical.rumi", "scene/img*[1,2].rumi", "mask.bin"],
        metadata=taco.MetadataSchema(taco.Level("children/scene", rumi=taco.extensions.Rumi())),
    )
    output = tmp_path / "rumi.zip"
    with taco.open_writer(collection(contract), output) as writer:
        writer.add(
            taco.Sample(
                id="s0",
                assets=[
                    taco.Asset(tmp_path / "optical.rumi", path="scene/optical.rumi"),
                    taco.Asset(tmp_path / "img1.rumi", path="scene/img1.rumi"),
                    taco.Asset(tmp_path / "img0.rumi", path="scene/img0.rumi"),
                    taco.Asset(tmp_path / "mask.bin", path="mask.bin"),
                ],
            )
        )
        writer.run()

    # Python builds its own pivot; R and Julia run the core query directly.
    core = engine.open_reader().execute(native_sql(output)).to_arrow_table()
    for table in (taco.read(output), core):
        row = table.to_pylist()[0]
        assert row["scene__optical.rumi::location"].startswith("/vsisubfile/")
        assert row["scene__optical.rumi::header"] == b"optical.rumi"
        assert row["scene__img::header"] == [b"img0.rumi", b"img1.rumi"]
        assert len(row["scene__img::location"]) == 2
        assert row["mask.bin::location"].startswith("/vsisubfile/")
        assert "mask.bin::header" not in row

    quiet = engine.open_reader().execute(native_sql(output, location=False)).to_arrow_table().to_pylist()[0]
    assert quiet["scene__optical.rumi::header"] is None
    assert "scene__optical.rumi::header" in taco.open_dataset(output).sql("SELECT * FROM dataset").column_names
