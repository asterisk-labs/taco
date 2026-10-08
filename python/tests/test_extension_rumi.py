from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pytest
from pydantic import BaseModel

import taco
from taco.container.view import open_view
from taco.errors import ContractError, SampleError


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


def write_fake_rumi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    array: np.ndarray,
    *,
    stats: bool | Sequence[str] = True,
) -> dict[str, object]:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake_rumi(monkeypatch, array)
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=[taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi(stats=stats))],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u10", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    return open_view(tmp_path / "dataset").level("sample").to_pylist()[0]


IMAGE = np.array([[[1, 2], [3, np.nan]], [[5, 5], [5, 5]]], dtype=np.float32)
# Two time steps of two bands. NaN and infinity are never valid.
CUBE = np.array(
    [
        [[[1, np.nan]], [[5, np.nan]]],
        [[[3, np.inf]], [[7, np.nan]]],
    ],
    dtype=np.float32,
)


def test_rumi_stats_contract_matches_the_spec() -> None:
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=[taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi(stats=True))],
    )
    fields = contract.metadata["sample"]
    assert list(fields) == [
        "rumi:header",
        "rumi:minimum",
        "rumi:maximum",
        "rumi:mean",
        "rumi:stddev",
        "rumi:p2",
        "rumi:p98",
    ]
    assert {fields[name].type for name in list(fields)[1:]} == {"double"}
    assert all(fields[name].nullable for name in list(fields)[1:])
    assert fields["rumi:p98"].description == "98th percentile of all valid values"


def test_rumi_named_statistics_become_columns_in_order() -> None:
    rumi = taco.extensions.sample.rumi.Rumi(stats=["p98_t0_b3", "mean", "stddev_b10", "minimum_t2"])
    contract = taco.Contract(structure=["data.rumi"], metadata=[taco.Level("sample", rumi=rumi)])
    assert rumi.stats == ("p98_t0_b3", "mean", "stddev_b10", "minimum_t2")
    assert list(contract.metadata["sample"]) == [
        "rumi:header",
        "rumi:p98_t0_b3",
        "rumi:mean",
        "rumi:stddev_b10",
        "rumi:minimum_t2",
    ]
    descriptions = {name: field.description for name, field in contract.metadata["sample"].items()}
    assert descriptions["rumi:p98_t0_b3"] == "98th percentile of the valid values in band 3 at time step 0"
    assert descriptions["rumi:stddev_b10"] == "Population standard deviation of the valid values in band 10"
    assert descriptions["rumi:minimum_t2"] == "Minimum of the valid values at time step 2"


def test_rumi_accepts_one_statistic_name() -> None:
    assert taco.extensions.sample.rumi.Rumi(stats="mean_b0").stats == ("mean_b0",)


def test_rumi_selection_stays_hashable() -> None:
    assert hash(taco.extensions.sample.rumi.Rumi(stats=["mean", "p2"])) == hash(
        taco.extensions.sample.rumi.Rumi(stats=("mean", "p2"))
    )
    assert hash(taco.extensions.sample.rumi.Rumi(stats={"data.rumi": ["mean"]}))


def test_rumi_can_select_statistics_per_file() -> None:
    contract = taco.Contract(
        structure=["scene/image.rumi", "scene/cube.rumi"],
        metadata=[
            taco.Level(
                "children/scene",
                rumi=taco.extensions.sample.rumi.Rumi(
                    stats={
                        "scene/image.rumi": ["mean", "p98"],
                        "scene/cube.rumi": ["mean_t0", "p98_t0_b3"],
                    }
                ),
            )
        ],
    )
    fields = contract.metadata["children/scene"]
    assert fields["rumi:mean"].files == ("scene/image.rumi",)
    assert fields["rumi:p98"].files == ("scene/image.rumi",)
    assert fields["rumi:mean_t0"].files == ("scene/cube.rumi",)
    assert fields["rumi:p98_t0_b3"].files == ("scene/cube.rumi",)
    assert fields["rumi:header"].files is None
    assert contract.to_dict()["taco:metadata"]["children/scene"]["rumi:mean"]["files"] == ["scene/image.rumi"]


def test_rumi_per_file_selection_validates_the_contract() -> None:
    with pytest.raises(ValueError, match="mapping cannot be empty"):
        taco.extensions.sample.rumi.Rumi(stats={})
    with pytest.raises(ValueError, match="omit it from the mapping"):
        taco.extensions.sample.rumi.Rumi(stats={"data.rumi": False})
    with pytest.raises(ValueError, match="omit it from the mapping"):
        taco.extensions.sample.rumi.Rumi(stats={"data.rumi": []})
    with pytest.raises(TypeError, match="keys must be structure declarations"):
        taco.extensions.sample.rumi.Rumi(stats={1: "mean"})  # type: ignore[dict-item]

    for selected, message in [
        ({"missing.rumi": "mean"}, "unknown structure declarations"),
        ({"other/data.rumi": "mean"}, "outside that metadata level"),
    ]:
        with pytest.raises(ContractError, match=message):
            taco.Contract(
                structure=["scene/data.rumi", "other/data.rumi"],
                metadata=[
                    taco.Level(
                        "children/scene",
                        rumi=taco.extensions.sample.rumi.Rumi(stats=selected),
                    )
                ],
            )


def test_rumi_file_statistics_survive_reconstruction() -> None:
    selected = {
        "image.rumi": ["mean", "p98"],
        "cube.rumi": ["mean_t0"],
    }
    extension = taco.extensions.sample.rumi.Rumi(stats=selected)

    changed = replace(extension, header=False)
    reconstructed = taco.extensions.sample.rumi.Rumi(stats=extension.stats)

    assert changed.stats == extension.stats
    assert changed.configuration() == {"header": False, "stats": selected}
    assert reconstructed == extension
    assert hash(reconstructed) == hash(extension)
    assert reconstructed.configuration() == extension.configuration()


@pytest.mark.parametrize(
    ("name", "files", "message"),
    [
        ("rumi:mean", "data.rumi", "non-empty list"),
        ("rumi:mean", [], "non-empty list"),
        ("rumi:mean", ["data.rumi", "data.rumi"], "must not contain duplicates"),
        ("rumi:header", ["data.rumi"], "cannot be restricted to files"),
        ("quality:score", ["data.rumi"], "cannot be restricted to files"),
    ],
)
def test_rumi_file_scopes_are_validated(name: str, files: object, message: str) -> None:
    declaration = {
        "type": "binary" if name == "rumi:header" else "double",
        "nullable": True,
        "description": "",
        "files": files,
    }
    with pytest.raises(ContractError, match=message):
        taco.Contract(structure=["data.rumi"], metadata={"children": {name: declaration}})


@pytest.mark.parametrize("name", ["avg", "Mean", "mean_b01", "mean_b", "mean_b3_t0", "mean_t0_", "p25", "valid_count"])
def test_rumi_rejects_unknown_statistics(name: str) -> None:
    with pytest.raises(ValueError, match="unknown Rumi statistic"):
        taco.extensions.sample.rumi.Rumi(stats=[name])


def test_rumi_rejects_a_repeated_statistic() -> None:
    with pytest.raises(ValueError, match="duplicate Rumi statistic 'mean'"):
        taco.extensions.sample.rumi.Rumi(stats=["mean", "p2", "mean"])


def test_rumi_rejects_an_empty_statistic_list() -> None:
    with pytest.raises(ValueError, match="use stats=False"):
        taco.extensions.sample.rumi.Rumi(stats=[])


@pytest.mark.parametrize(
    ("stats", "message"),
    [
        (1, "mapping from structure declarations"),
        (None, "mapping from structure declarations"),
        (b"mean", "mapping from structure declarations"),
        (["mean", 2], "statistic names must be strings"),
        ({"data.rumi": 2}, "sequence of statistic names"),
    ],
)
def test_rumi_rejects_statistics_that_are_not_names(stats: object, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        taco.extensions.sample.rumi.Rumi(stats=stats)  # type: ignore[arg-type]


def test_rumi_statistics_cover_the_whole_array_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = write_fake_rumi(tmp_path, monkeypatch, IMAGE)
    assert row["rumi:header"] == b"canonical-header"
    # Valid values: 1, 2, 3 and four fives. Percentiles interpolate linearly.
    assert row["rumi:minimum"] == 1.0
    assert row["rumi:maximum"] == 5.0
    assert row["rumi:mean"] == pytest.approx(26 / 7)
    assert row["rumi:stddev"] == pytest.approx(1.577908716741037)
    assert row["rumi:p2"] == pytest.approx(1.12)
    assert row["rumi:p98"] == 5.0


def test_rumi_band_statistics_read_one_band(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stats = ["mean_b0", "stddev_b0", "minimum_b1", "p98_b1"]
    row = write_fake_rumi(tmp_path, monkeypatch, IMAGE, stats=stats)
    assert row["rumi:mean_b0"] == 2.0
    assert row["rumi:stddev_b0"] == pytest.approx(0.816496580927726)
    assert row["rumi:minimum_b1"] == 5.0
    assert row["rumi:p98_b1"] == 5.0
    assert "rumi:mean" not in row


def test_rumi_cube_statistics_select_time_steps_and_bands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stats = ["mean", "maximum", "mean_b0", "mean_b1", "mean_t0", "mean_t1", "mean_t1_b0", "stddev_t0_b1"]
    row = write_fake_rumi(tmp_path, monkeypatch, CUBE, stats=stats)
    assert row["rumi:mean"] == 4.0
    assert row["rumi:maximum"] == 7.0
    assert row["rumi:mean_b0"] == 2.0
    assert row["rumi:mean_b1"] == 6.0
    assert row["rumi:mean_t0"] == 3.0
    assert row["rumi:mean_t1"] == 5.0
    assert row["rumi:mean_t1_b0"] == 3.0
    assert row["rumi:stddev_t0_b1"] == 0.0


def test_rumi_statistics_without_valid_values_are_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    array = np.array([[[1, 2]], [[np.nan, np.nan]]], dtype=np.float32)
    row = write_fake_rumi(tmp_path, monkeypatch, array, stats=["mean", "mean_b1", "p2_b1"])
    assert row["rumi:mean"] == 1.5
    assert row["rumi:mean_b1"] is None
    assert row["rumi:p2_b1"] is None


def test_rumi_statistics_read_booleans_as_zero_and_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = write_fake_rumi(tmp_path, monkeypatch, np.array([[[True, False], [True, True]]]))
    assert (row["rumi:minimum"], row["rumi:maximum"], row["rumi:mean"]) == (0.0, 1.0, 0.75)
    assert (row["rumi:p2"], row["rumi:p98"]) == (pytest.approx(0.06), 1.0)


@pytest.mark.parametrize(
    "values",
    [
        np.random.default_rng(1).integers(0, 4000, 1000).astype(np.int16),
        np.random.default_rng(2).integers(-(2**15), 2**15, 1000).astype(np.int16),
        np.random.default_rng(3).integers(0, 2**32, 1000).astype(np.uint32),
        np.random.default_rng(4).integers(0, 60_000, 1000).astype(np.uint64) + np.uint64(2**64 - 70_000),
        np.array([7], dtype=np.uint8),
    ],
    ids=["counted", "full-int16", "wide-sorted", "top-of-uint64", "one-value"],
)
def test_rumi_integer_percentiles_match_numpy(
    values: np.ndarray, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = write_fake_rumi(tmp_path, monkeypatch, values.reshape(1, 1, -1))
    assert (row["rumi:p2"], row["rumi:p98"]) == (np.percentile(values, 2), np.percentile(values, 98))


def test_rumi_integer_percentiles_do_not_wrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # numpy.percentile subtracts these neighbours as int16 and wraps to -1.
    values = np.array([-(2**15)] + [2**15 - 1] * 49, dtype=np.int16).reshape(1, 1, -1)
    row = write_fake_rumi(tmp_path, monkeypatch, values)
    assert row["rumi:p2"] == pytest.approx(32767 - 65535 * 0.02)


def test_rumi_statistics_reject_complex_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SampleError, match="need real values, got complex64"):
        write_fake_rumi(tmp_path, monkeypatch, np.ones((1, 2, 2), dtype=np.complex64))


def test_rumi_time_statistics_need_a_cube(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SampleError, match=r"'mean_t0' needs a Cube, but 'dem\.rumi' is an Image"):
        write_fake_rumi(tmp_path, monkeypatch, IMAGE, stats=["mean_t0"])


def test_rumi_statistics_reject_a_missing_band(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SampleError, match=r"'p2_b2' reads band 2, but 'dem\.rumi' has 2 bands"):
        write_fake_rumi(tmp_path, monkeypatch, IMAGE, stats=["p2_b2"])


def test_rumi_statistics_reject_a_missing_time_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SampleError, match=r"'mean_t2_b0' reads time step 2, but 'dem\.rumi' has 2 time steps"):
        write_fake_rumi(tmp_path, monkeypatch, CUBE, stats=["mean_t2_b0"])


def test_rumi_statistics_reject_arrays_that_are_not_images_or_cubes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SampleError, match=r"\(B,Y,X\) or \(T,B,Y,X\), got \(2, 2\)"):
        write_fake_rumi(tmp_path, monkeypatch, np.ones((2, 2)))


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
        metadata=[taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi())],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u11", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["rumi:header"] == b"header-only"
    assert [name for name in row if name.startswith("rumi:")] == ["rumi:header"]


def test_rumi_extension_can_store_stats_without_the_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake_rumi(monkeypatch, np.ones((1, 2, 2), dtype=np.int16))
    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=[taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi(header=False, stats=True))],
    )
    assert "rumi:header" not in contract.metadata["sample"]
    output = tmp_path / "dataset.zip"
    with taco.open_writer(collection(contract), output) as writer:
        writer.add(taco.Sample(id="u15", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    row = taco.read(output).to_pylist()[0]
    assert row["rumi:mean"] == 1.0
    assert "data.rumi::header" not in row


def test_rumi_extension_rejects_an_empty_configuration() -> None:
    with pytest.raises(ValueError, match="header=True or at least one statistic"):
        taco.extensions.sample.rumi.Rumi(header=False)


def test_rumi_extension_must_use_the_rumi_namespace() -> None:
    with pytest.raises(ContractError, match="Rumi must use metadata namespace 'rumi', got 'r'"):
        taco.Level("sample", r=taco.extensions.sample.rumi.Rumi())


def test_rumi_namespace_is_reserved_for_the_extension() -> None:
    class Notes(BaseModel):
        mean: str

    for value in (Notes, Notes | None):
        with pytest.raises(ContractError, match="reserved for the rumi extension"):
            taco.Level("sample", rumi=value)


def test_rumi_fields_keep_the_types_wide_reads_expect() -> None:
    for name, declared in (("rumi:mean_b0", "string"), ("rumi:header", "string")):
        with pytest.raises(ContractError, match=f"children.{name} must have type"):
            taco.Contract(
                structure=["data.rumi"],
                metadata={"children": {name: {"type": declared, "nullable": True, "description": ""}}},
            )


def test_rumi_extension_runs_at_asset_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "dem.rumi"
    source.write_bytes(b"RUMI fixture")
    fake_rumi(monkeypatch, np.ones((1, 2, 2), dtype=np.int16), header=b"asset-header")
    contract = taco.Contract(
        structure=["dem.rumi"],
        metadata=[taco.Level("children", rumi=taco.extensions.sample.rumi.Rumi())],
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
        metadata=[taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi())],
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
    _, expected_header = rumi.write(source, frames, bands=["elevation"], time=["2024-01-01"])

    contract = taco.Contract(
        structure=["data.rumi"],
        metadata=[taco.Level("sample", rumi=taco.extensions.sample.rumi.Rumi(stats=True))],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(taco.Sample(id="u14", assets=taco.Asset(source, path="data.rumi")))
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["rumi:header"] == expected_header
    assert row["rumi:mean"] == 7.5
    assert (row["rumi:minimum"], row["rumi:maximum"]) == (0.0, 15.0)
    assert (row["rumi:p2"], row["rumi:p98"]) == (pytest.approx(0.3), pytest.approx(14.7))


def test_wide_reads_carry_rumi_headers_and_statistics_next_to_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    means = {"optical.rumi": 1.0, "img0.rumi": 2.0, "img1.rumi": 3.0, "plain.rumi": 4.0}
    fake = SimpleNamespace(
        info=lambda *, source: SimpleNamespace(header=Path(source).name.encode()),
        read=lambda source, header: np.full((1, 1, 2), means[Path(source).name]),
    )
    monkeypatch.setitem(sys.modules, "rumi", fake)
    for name in ("optical.rumi", "img0.rumi", "img1.rumi", "plain.rumi", "mask.bin"):
        (tmp_path / name).write_bytes(name.encode())
    contract = taco.Contract(
        structure=["scene/optical.rumi", "scene/img*[1,2].rumi", "scene/plain.rumi", "mask.bin"],
        metadata=[
            taco.Level(
                "children/scene",
                rumi=taco.extensions.sample.rumi.Rumi(
                    stats={
                        "scene/optical.rumi": "mean",
                        "scene/img*[1,2].rumi": "maximum_b0",
                    }
                ),
            )
        ],
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
                    taco.Asset(tmp_path / "plain.rumi", path="scene/plain.rumi"),
                    taco.Asset(tmp_path / "mask.bin", path="mask.bin"),
                ],
            )
        )
        writer.run()

    # Compare the public reader with direct execution of the core-generated SQL.
    core = engine.open_reader().execute(native_sql(output)).to_arrow_table()
    for table in (taco.read(output), core):
        assert table.column_names[2:7] == [
            "scene/optical.rumi::location",
            "scene/optical.rumi::header",
            "scene/optical.rumi::mean",
            "scene/img::location",
            "scene/img::header",
        ]
        row = table.to_pylist()[0]
        assert row["scene/optical.rumi::location"].startswith("/vsisubfile/")
        assert row["scene/optical.rumi::header"] == b"optical.rumi"
        assert row["scene/optical.rumi::mean"] == 1.0
        assert row["scene/img::header"] == [b"img0.rumi", b"img1.rumi"]
        assert row["scene/img::maximum_b0"] == [2.0, 3.0]
        assert "scene/optical.rumi::maximum_b0" not in row
        assert "scene/img::mean" not in row
        assert row["scene/plain.rumi::header"] == b"plain.rumi"
        assert "scene/plain.rumi::mean" not in row
        assert "scene/plain.rumi::maximum_b0" not in row
        assert len(row["scene/img::location"]) == 2
        assert row["mask.bin::location"].startswith("/vsisubfile/")
        assert not any(name.startswith("mask.bin::") and name != "mask.bin::location" for name in row)

    plain = open_view(output).level("children/scene").to_pylist()[-1]
    assert plain["internal:relative_path"] == "0/scene/plain.rumi"
    assert plain["rumi:mean"] is None
    assert plain["rumi:maximum_b0"] is None

    assert taco.read(output, files="mask.bin").column_names == ["taco:sample_index", "id", "mask.bin::location"]
    quiet = engine.open_reader().execute(native_sql(output, location=False)).to_arrow_table()
    assert quiet.schema.field("scene/optical.rumi::mean").type == pa.float64()
    assert quiet.schema.field("scene/img::maximum_b0").type == pa.list_(pa.float64())
    assert quiet.to_pylist()[0]["scene/optical.rumi::header"] is None
    assert "scene/optical.rumi::header" in taco.open_dataset(output).sql("SELECT * FROM dataset").column_names


def test_contract_rejects_legacy_rumi_stats() -> None:
    stats = {"type": "list<struct<mean: double?>>", "nullable": True, "description": "Per-band statistics"}
    with pytest.raises(ContractError, match="rumi:stats is not a Rumi header or statistic"):
        taco.Contract(structure=["data.rumi"], metadata={"children": {"rumi:stats": stats}})
