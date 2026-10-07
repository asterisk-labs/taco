"""What `taco.ml.Dataset` makes of each kind of slot, on small real archives."""

from __future__ import annotations

import io
import itertools
import struct
import wave
from pathlib import Path

import numpy as np
import pytest
from pydantic import BaseModel

import taco
from taco.metadata.ml import SlotKind, Task
from taco.ml import Dataset
from taco.ml.dataset import _wave

rasterio = pytest.importorskip("rasterio")

_SAMPLE_IDS = itertools.count()


def _sample(**fields) -> taco.Sample:
    """A sample with a fresh id; the fixtures care about content, not identity."""
    return taco.Sample(id=f"s{next(_SAMPLE_IDS)}", **fields)



def _tif(path: Path, array: np.ndarray, *, nodata: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    bands, height, width = array.shape
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=bands,
                       dtype=array.dtype, nodata=nodata, crs="EPSG:4326",
                       transform=rasterio.transform.from_origin(0, height, 1, 1)) as sink:
        sink.write(array)
    return path


def _archive(path: Path, structure: list[str], contract: dict, samples: list[taco.Sample],
             *levels: taco.Level) -> Path:
    collection = taco.Collection(
        contract=taco.Contract(structure=structure, metadata=list(levels)),
        id="reading", description="Fixture for taco.ml reading",
        licenses=["CC-BY-4.0"], providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
        ml={"contract": contract},
    )
    with taco.open_writer(collection, path) as writer:
        writer.extend(samples)
        writer.run()
    return path


class Boxes(BaseModel):
    boxes: list[float]
    per_query: list[int]


class Frames(BaseModel):
    frames: int


class Date(BaseModel):
    date: str


def test_names_print_as_they_are_stored() -> None:
    assert str(Task.COUNTING) == f"{Task.COUNTING}" == "counting"
    assert f"{SlotKind.MASK:>6}" == "  mask"


def test_boxes_come_back_one_per_row_and_their_counts_must_add_up(tmp_path: Path) -> None:
    image = _tif(tmp_path / "src" / "image.tif", np.zeros((1, 4, 4), "uint8"))
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif"}],
                "targets": [{"name": "boxes", "kind": "bbox_2d", "field": "boxes",
                             "counts_field": "per_query"}]}

    def build(name: str, counts: list[int]) -> Dataset:
        sample = _sample(metadata=taco.Metadata(ml=Boxes(boxes=[0, 0, 1, 1, 2, 2, 3, 3],
                                                             per_query=counts)),
                             assets=[taco.Asset(image, path="image.tif")])
        return Dataset(_archive(tmp_path / name, ["image.tif"], contract, [sample],
                                taco.Level("sample", ml=Boxes)))

    value = build("good.zip", [1, 1])[0]["boxes"]
    assert value.array.shape == (2, 4)
    assert value.counts == [1, 1]
    with pytest.raises(ValueError, match="adds up to 3"):
        build("bad.zip", [1, 2])[0]


def test_masked_hides_nodata_and_ignored_labels(tmp_path: Path) -> None:
    image = _tif(tmp_path / "src" / "image.tif", np.array([[[0, 5], [6, 7]]], "uint16"))
    label = _tif(tmp_path / "src" / "label.tif", np.array([[[1, 255], [0, 1]]], "uint8"))
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif", "nodata": 0}],
                "targets": [{"name": "label", "kind": "mask", "path": "label.tif",
                             "classes": ["a", "b"], "ignore_index": 255}]}
    sample = _sample(assets=[taco.Asset(image, path="image.tif"),
                                 taco.Asset(label, path="label.tif")])
    path = _archive(tmp_path / "x.zip", ["image.tif", "label.tif"], contract, [sample],
                    taco.Level("sample"))
    masked = Dataset(path, masked=True)[0]
    assert masked["image"].valid.tolist() == [[[False, True], [True, True]]]
    assert masked["label"].valid.tolist() == [[True, False], [True, True]]
    assert not np.ma.isMaskedArray(Dataset(path)[0]["image"].array)


def test_physical_values_leave_nodata_out(tmp_path: Path) -> None:
    # The sentinel scaled is a plausible-looking value (-3.2768), so it must not survive.
    image = _tif(tmp_path / "src" / "image.tif", np.array([[[-32768, 100], [200, 300]]], "int16"))
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif",
                            "calibration": "scaled", "scale_factor": 1e-4, "nodata": -32768}],
                "targets": []}
    sample = _sample(assets=[taco.Asset(image, path="image.tif")])
    path = _archive(tmp_path / "x.zip", ["image.tif"], contract, [sample], taco.Level("sample"))
    plain = Dataset(path)[0]["image"].physical
    assert not np.ma.isMaskedArray(plain)
    assert np.isnan(plain[0, 0, 0])
    assert np.allclose(plain[0].ravel()[1:], [0.01, 0.02, 0.03])
    masked = Dataset(path, masked=True)[0]["image"].physical
    assert np.ma.getmaskarray(masked).tolist() == [[[True, False], [False, False]]]
    assert np.allclose(masked.compressed(), [0.01, 0.02, 0.03])


def test_band_nodata_is_masked_on_its_own_band(tmp_path: Path) -> None:
    # As geonrw declares its elevation target: -9999 on the band, none on the slot.
    elevation = _tif(tmp_path / "src" / "dem.tif", np.array([[[-9999, 50], [60, 70]]], "float32"))
    image = _tif(tmp_path / "src" / "image.tif",
                 np.array([[[0, 1], [2, 3]], [[0, 5], [5, 0]]], "uint16"))
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif", "nodata": 3,
                            "bands": [{"index": 0}, {"index": 1, "nodata": 5}]}],
                "targets": [{"name": "elevation", "kind": "raster", "path": "dem.tif",
                             "calibration": "physical", "units": "m",
                             "bands": [{"index": 0, "nodata": -9999}]}]}
    sample = _sample(assets=[taco.Asset(image, path="image.tif"),
                                 taco.Asset(elevation, path="dem.tif")])
    path = _archive(tmp_path / "x.zip", ["image.tif", "dem.tif"], contract, [sample],
                    taco.Level("sample"))
    masked = Dataset(path, masked=True)[0]
    assert masked["elevation"].valid.tolist() == [[[False, True], [True, True]]]
    assert masked["elevation"].physical.compressed().tolist() == [50, 60, 70]
    # The slot's nodata applies to every band; a band's only to that band.
    assert masked["image"].valid.tolist() == [[[True, True], [True, False]],
                                              [[True, False], [False, True]]]
    plain = Dataset(path)[0]
    assert not np.ma.isMaskedArray(plain["elevation"].array)
    assert np.isnan(plain["elevation"].physical[0, 0, 0])
    assert np.allclose(plain["elevation"].physical[0].ravel()[1:], [50, 60, 70])


def test_masked_hides_ignored_classes_beside_the_ignore_index(tmp_path: Path) -> None:
    label = _tif(tmp_path / "src" / "label.tif", np.array([[[0, 1], [2, 3]]], "uint8"))
    contract = {"inputs": [],
                "targets": [{"name": "label", "kind": "mask", "path": "label.tif",
                             "classes": ["unlabelled", "a", "unused", "b"],
                             "ignore_index": 0, "ignore_classes": [2]}]}
    sample = _sample(assets=[taco.Asset(label, path="label.tif")])
    path = _archive(tmp_path / "x.zip", ["label.tif"], contract, [sample], taco.Level("sample"))
    assert Dataset(path, masked=True)[0]["label"].valid.tolist() == [[False, True], [False, True]]


def test_a_series_stacked_in_one_file_is_cut_by_its_frame_count(tmp_path: Path) -> None:
    stack = np.arange(6 * 2 * 2, dtype="uint8").reshape(6, 2, 2)      # 3 frames x 2 bands
    series = _tif(tmp_path / "src" / "series.tif", stack)
    contract = {"inputs": [{"name": "series", "kind": "raster_series", "path": "series.tif",
                            "frames_field": "frames",
                            "bands": [{"index": 0}, {"index": 1}]}]}
    sample = _sample(metadata=taco.Metadata(ml=Frames(frames=3)),
                         assets=[taco.Asset(series, path="series.tif")])
    path = _archive(tmp_path / "x.zip", ["series.tif"], contract, [sample],
                    taco.Level("sample", ml=Frames))
    array = Dataset(path)[0]["series"].array
    assert array.shape == (3, 2, 2, 2)
    assert array[2, 1].tolist() == stack[5].tolist()


def test_per_frame_dates_follow_the_frame_numbers(tmp_path: Path) -> None:
    # Twelve frames, so the stored rows (t0, t1, t10, t11, t2, ...) and the frame
    # numbers disagree about the order.
    frames = [taco.Asset(_tif(tmp_path / "src" / f"t{i}.tif", np.full((1, 2, 2), i, "uint8")),
                         path=f"s2/t{i}.tif", metadata=taco.Metadata(ml=Date(date=f"2020-{i + 1:02d}-01")))
              for i in range(12)]
    contract = {"inputs": [{"name": "s2", "kind": "raster_series", "path": "s2/t*[1,12].tif",
                            "structure": "series", "time_field": "children/s2:date"}]}
    sample = _sample(assets=frames)
    path = _archive(tmp_path / "x.zip", ["s2/t*[1,12].tif"], contract, [sample],
                    taco.Level("sample"), taco.Level("children/s2", ml=Date))
    value = Dataset(path)[0]["s2"]
    assert [int(frame[0, 0, 0]) for frame in value.array] == list(range(12))
    assert value.times == [f"2020-{i + 1:02d}-01" for i in range(12)]


def test_a_folder_container_reads_like_its_zip(tmp_path: Path) -> None:
    # The same samples written as ZIP and as FOLDER decode to the same arrays: a
    # folder's payload is DATA/<relative_path> itself, with no byte range to seek.
    def samples(root: Path) -> list[taco.Sample]:
        out = []
        for index in range(3):
            image = _tif(root / f"image{index}.tif", np.full((2, 3, 3), index, "uint8"))
            frames = [taco.Asset(_tif(root / f"t{index}_{i}.tif", np.full((1, 2, 2), 10 * index + i, "uint8")),
                                 path=f"s2/t{i}.tif") for i in range(index + 1)]
            out.append(_sample(assets=[taco.Asset(image, path="image.tif"), *frames]))
        return out
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif"},
                           {"name": "s2", "kind": "raster_series", "path": "s2/t*[1,3].tif",
                            "structure": "series"}]}
    structure = ["image.tif", "s2/t*[1,3].tif"]
    levels = (taco.Level("sample"), taco.Level("children"), taco.Level("children/s2"))
    zipped = Dataset(_archive(tmp_path / "x.zip", structure, contract, samples(tmp_path / "a"), *levels))
    folder = Dataset(_archive(tmp_path / "x", structure, contract, samples(tmp_path / "b"), *levels))
    assert Path(folder.path).is_dir()
    assert len(folder) == len(zipped) == 3
    for index in range(3):
        for name in ("image", "s2"):
            assert np.array_equal(np.asarray(folder[index][name].array),
                                  np.asarray(zipped[index][name].array))
    assert len(folder[2]["s2"].array) == 3


def test_a_tacocat_reads_like_its_partitions(tmp_path: Path) -> None:
    # Two partitions consolidated into `.tacocat/`: opening the catalog, or the
    # dataset directory holding it, gives the same samples in partition order as
    # the list of archives, series frames included.
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif"},
                           {"name": "s2", "kind": "raster_series", "path": "s2/t*[1,3].tif",
                            "structure": "series"}]}
    structure = ["image.tif", "s2/t*[1,3].tif"]
    levels = (taco.Level("sample"), taco.Level("children"), taco.Level("children/s2"))
    root = tmp_path / "ds"
    parts = []
    for part in range(2):
        samples = []
        for index in range(3):
            value = 10 * part + index
            image = _tif(tmp_path / "src" / f"i{value}.tif", np.full((1, 2, 2), value, "uint8"))
            frames = [taco.Asset(_tif(tmp_path / "src" / f"t{value}_{i}.tif",
                                      np.full((1, 2, 2), 100 + value, "uint8")), path=f"s2/t{i}.tif")
                      for i in range(index + 1)]
            samples.append(_sample(assets=[taco.Asset(image, path="image.tif"), *frames]))
        root.mkdir(exist_ok=True)
        parts.append(_archive(root / f"x.{part:04d}.zip", structure, contract, samples, *levels))
    catalog = taco.consolidate(parts)
    listed = Dataset(parts)
    for opened in (Dataset(catalog), Dataset(root)):
        assert len(opened) == len(listed) == 6
        for index in range(6):
            for name in ("image", "s2"):
                assert np.array_equal(np.asarray(opened[index][name].array),
                                      np.asarray(listed[index][name].array))
    assert [int(Dataset(root)[i]["image"].array[0, 0, 0]) for i in range(6)] == [0, 1, 2, 10, 11, 12]
    # A level below the sample resolves to the sample's own rows in every partition,
    # including after the first, where the catalog's ids no longer equal local ones.
    frames = [Dataset(root).lookup(i, "children/s2:internal:relative_path") for i in range(6)]
    assert frames == [listed.lookup(i, "children/s2:internal:relative_path") for i in range(6)]
    assert [len(f) for f in frames] == [1, 2, 3, 1, 2, 3]


def test_a_reference_names_a_level_only_when_it_is_one(tmp_path: Path) -> None:
    image = _tif(tmp_path / "src" / "image.tif", np.zeros((1, 2, 2), "uint8"))
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif"}],
                "targets": [{"name": "frames", "kind": "scalar", "field": "sample:frames"}]}
    sample = _sample(metadata=taco.Metadata(ml=Frames(frames=7)),
                         assets=[taco.Asset(image, path="image.tif")])
    dataset = Dataset(_archive(tmp_path / "x.zip", ["image.tif"], contract, [sample],
                               taco.Level("sample", ml=Frames)))
    assert dataset[0]["frames"].array == 7
    # `ml:frames` is a column name with a namespace, not a level called `ml`.
    assert dataset.lookup(0, "ml:frames") == dataset.lookup(0, "frames") == 7
    assert dataset.column("frames").to_pylist() == [7]


def test_read_decodes_only_the_slots_asked_for(tmp_path: Path) -> None:
    image = _tif(tmp_path / "src" / "image.tif", np.zeros((1, 2, 2), "uint8"))
    contract = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif"}],
                "targets": [{"name": "frames", "kind": "scalar", "field": "frames"}]}
    sample = _sample(metadata=taco.Metadata(ml=Frames(frames=7)),
                         assets=[taco.Asset(image, path="image.tif")])
    dataset = Dataset(_archive(tmp_path / "x.zip", ["image.tif"], contract, [sample],
                               taco.Level("sample", ml=Frames)))
    assert set(dataset.read(0, ["frames"])) == {"frames"}
    with pytest.raises(KeyError, match="no slot"):
        dataset.read(0, ["nothing"])


def test_wave_files_decode_to_channels_and_a_sample_rate() -> None:
    samples = np.array([[0, 16384], [-16384, 32767]], dtype="<i2")     # 2 frames x 2 channels
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(2)
        sink.setsampwidth(2)
        sink.setframerate(8000)
        sink.writeframes(samples.tobytes())
    array, rate = _wave(buffer.getvalue(), "clip.wav")
    assert rate == 8000
    assert array.shape == (2, 2)
    assert array[0].tolist() == pytest.approx([0.0, -0.5])
    assert struct.unpack("<4s", buffer.getvalue()[:4])[0] == b"RIFF"


def test_a_large_raster_is_read_smaller_only_when_nothing_depends_on_its_size(tmp_path: Path) -> None:
    image = _tif(tmp_path / "src" / "image.tif", np.arange(64, dtype="uint8").reshape(1, 8, 8))
    alone = {"inputs": [{"name": "image", "kind": "raster", "path": "image.tif"}]}
    sample = _sample(assets=[taco.Asset(image, path="image.tif")])
    path = _archive(tmp_path / "x.zip", ["image.tif"], alone, [sample], taco.Level("sample"))
    assert Dataset(path)[0]["image"].array.shape == (1, 8, 8)
    assert Dataset(path, max_pixels=16)[0]["image"].array.shape == (1, 4, 4)

    boxed = dict(alone, targets=[{"name": "boxes", "kind": "bbox_2d", "field": "boxes"}])
    with_boxes = _sample(metadata=taco.Metadata(ml=Boxes(boxes=[0, 0, 8, 8], per_query=[1])),
                             assets=[taco.Asset(image, path="image.tif")])
    path = _archive(tmp_path / "y.zip", ["image.tif"], boxed, [with_boxes],
                    taco.Level("sample", ml=Boxes))
    # The boxes are in the full-size picture's pixels, so it stays full size.
    assert Dataset(path, max_pixels=16)[0]["image"].array.shape == (1, 8, 8)


def test_frames_decodes_only_the_chosen_files_of_a_series(tmp_path: Path) -> None:
    frames = [taco.Asset(_tif(tmp_path / "src" / f"t{i}.tif", np.full((1, 2, 2), i, "uint8")),
                         path=f"s2/t{i}.tif", metadata=taco.Metadata(ml=Date(date=f"2020-{i + 1:02d}-01")))
              for i in range(12)]
    contract = {"inputs": [{"name": "s2", "kind": "raster_series", "path": "s2/t*[1,12].tif",
                            "structure": "series", "time_field": "children/s2:date"}]}
    path = _archive(tmp_path / "x.zip", ["s2/t*[1,12].tif"], contract, [_sample(assets=frames)],
                    taco.Level("sample"), taco.Level("children/s2", ml=Date))
    dataset = Dataset(path)
    value = dataset.read(0, frames={"s2": [10, 3]})["s2"]
    assert [int(frame[0, 0, 0]) for frame in value.array] == [10, 3]
    assert value.times == ["2020-11-01", "2020-04-01"]
    with pytest.raises(IndexError, match="outside"):
        dataset.read(0, frames={"s2": [12]})
    with pytest.raises(KeyError, match="no slot"):
        dataset.read(0, frames={"nothing": [0]})
