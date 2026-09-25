from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

import pyarrow as pa
from pydantic import BaseModel

import taco


class SampleInfo(BaseModel):
    name: str
    score: float | None = None


class NodeInfo(BaseModel):
    kind: str


class VectorInfo(BaseModel):
    geometry_type: str


class BlobInfo(BaseModel):
    checksum: bytes


class RichDetail(BaseModel):
    name: str
    score: float | None


class RichMetadata(BaseModel):
    flag: bool
    payload: bytes
    timestamp: Annotated[datetime, pa.timestamp("us", tz="UTC")]
    day: date
    amount: Decimal
    tags: list[str]
    shape: tuple[int, int]
    detail: RichDetail
    lookup: dict[str, int]
    optional: float | None = None


class Benchmark(BaseModel):
    source: str
    revision: int


class CloudSTAC(taco.metadata.sample.STAC):
    cloud_cover: float


@dataclass(frozen=True)
class DatasetCase:
    name: str
    collection: taco.Collection
    samples: tuple[taco.Sample, ...]
    levels: tuple[str, ...]
    files: tuple[tuple[str | None, ...], ...]
    row_counts: tuple[int, ...]

    @property
    def data_paths(self) -> tuple[str, ...]:
        return tuple(
            str(index) if path is None else f"{index}/{path}"
            for index, paths in enumerate(self.files)
            for path in paths
        )


def point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def polygon(west: float, south: float, east: float, north: float) -> bytes:
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    return struct.pack("<BIII", 1, 3, 1, len(ring)) + b"".join(struct.pack("<dd", x, y) for x, y in ring)


def stac(
    index: int,
    model: type[taco.metadata.sample.STAC] | type[taco.metadata.sample.ISTAC] = taco.metadata.sample.STAC,
    **values: object,
) -> taco.metadata.sample.STAC | taco.metadata.sample.ISTAC:
    x = -76.0 + index
    common = dict(
        crs="EPSG:4326",
        centroid=point(x, -12.0),
        time_start=datetime(2024, 1, index + 1, tzinfo=timezone.utc),
        **values,
    )
    if issubclass(model, taco.metadata.sample.ISTAC):
        return model(geometry=polygon(x - 0.1, -12.1, x + 0.1, -11.9), **common)
    return model(
        tensor_shape=(13, 256, 256),
        geotransform=(x - 0.1, 0.2 / 256, 0, -11.9, 0, -0.2 / 256),
        **common,
    )


def spatial_profile(
    profile: str,
    index: int,
    model: type[BaseModel],
) -> BaseModel:
    x = -76.0 + index
    if profile == "spatial":
        return model(
            crs="EPSG:4326",
            tensor_shape=(1, 16, 16),
            geotransform=(x - 0.1, 0.2 / 16, 0, -11.9, 0, -0.2 / 16),
        )
    if profile == "ispatial":
        return model(crs="EPSG:4326", geometry=polygon(x - 0.1, -12.1, x + 0.1, -11.9))
    return model(
        time_start=datetime(2024, 1, index + 1, tzinfo=timezone.utc),
        time_end=datetime(2024, 1, index + 2, tzinfo=timezone.utc),
    )


def asset(dataset: str, index: int, path: str, **metadata: BaseModel) -> taco.Asset:
    content = f"{dataset}:{index}:{path}".encode()
    return taco.Asset(content, path=path, metadata=taco.Metadata(**metadata))


def collection(
    name: str,
    contract: taco.Contract,
    *,
    metadata: taco.CollectionMetadata | None = None,
    tasks: list[str] | None = None,
) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id=name,
        description=f"Writer regression case: {name}",
        licenses=["MIT"],
        providers=[{"name": "TACO tests", "roles": ["producer"]}],
        tasks=tasks or ["other"],
        metadata=metadata,
    )


def single_file() -> DatasetCase:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", core=SampleInfo)],
    )
    samples = tuple(
        taco.Sample(
            id=f"s{index}",
            assets=f"single-{index}".encode(),
            metadata=taco.Metadata(core=SampleInfo(name=f"sample-{index}", score=index / 2)),
        )
        for index in range(2)
    )
    return DatasetCase(
        "single_file",
        collection("single-file", contract),
        samples,
        contract.levels,
        (("data.bin",), ("data.bin",)),
        (2, 2),
    )


def flat_assets() -> DatasetCase:
    contract = taco.Contract(
        structure=["image.tif", "label.tif"],
        metadata=[taco.Level("sample", ml=taco.metadata.sample.Split)],
    )
    samples = tuple(
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(ml=taco.metadata.sample.Split(split="train" if index == 0 else "validation")),
            assets=[asset("flat", index, "image.tif"), asset("flat", index, "label.tif")],
        )
        for index in range(2)
    )
    metadata = taco.CollectionMetadata(labels=taco.metadata.collection.Labels(classes=["clear", "cloud"]))
    files = (("image.tif", "label.tif"),) * 2
    return DatasetCase(
        "flat_assets", collection("flat-assets", contract, metadata=metadata), samples, contract.levels, files, (2, 4)
    )


def nested_folders() -> DatasetCase:
    paths = (
        "before/B02.tif",
        "before/B03.tif",
        "before/B04.tif",
        "after/B02.tif",
        "after/B03.tif",
        "after/B04.tif",
        "change_map.tif",
    )
    contract = taco.Contract(
        structure=paths,
        metadata=[
            taco.Level("sample", core=SampleInfo),
            taco.Level(
                "children",
                stac=taco.metadata.folder.STAC | None,
            ),
        ],
    )
    samples = []
    for index in range(2):
        assets = [asset("nested", index, path) for path in paths]
        samples.append(
            taco.Sample(
                id=f"s{index}",
                metadata=taco.Metadata(core=SampleInfo(name=f"change-{index}")),
                folders=[
                    taco.Folder("before", metadata=taco.Metadata(stac=stac(index, taco.metadata.folder.STAC))),
                    taco.Folder("after", metadata=taco.Metadata(stac=stac(index + 1, taco.metadata.folder.STAC))),
                ],
                assets=assets,
            )
        )
    return DatasetCase(
        "nested_folders",
        collection("nested-folders", contract, tasks=["change-detection"]),
        tuple(samples),
        contract.levels,
        (paths, paths),
        (2, 6, 6, 6),
    )


def variable_sequence() -> DatasetCase:
    contract = taco.Contract(
        structure=["image*[1,4].tif"],
        metadata=[
            taco.Level("sample", core=SampleInfo),
        ],
    )
    files = (
        ("image0.tif",),
        ("image0.tif", "image1.tif"),
        tuple(f"image{index}.tif" for index in range(4)),
    )
    samples = tuple(
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(core=SampleInfo(name=f"sequence-{index}", score=float(len(paths)))),
            assets=[asset("sequence", index, path) for path in paths],
        )
        for index, paths in enumerate(files)
    )
    return DatasetCase(
        "variable_sequence",
        collection("variable-sequence", contract),
        samples,
        contract.levels,
        files,
        (3, 7),
    )


def mixed_structure() -> DatasetCase:
    structure = [
        "reference.tif",
        "images/frame*[2,6].tif",
        "labels/mask.tif",
        "labels/instance*[1,3].geojson",
    ]
    contract = taco.Contract(
        structure=structure,
        metadata=[
            taco.Level("sample", core=SampleInfo),
            taco.Level("children", node=NodeInfo),
            taco.Level(
                "children/labels",
                vector=VectorInfo | None,
            ),
        ],
    )
    files = (
        (
            "reference.tif",
            "images/frame0.tif",
            "images/frame1.tif",
            "labels/mask.tif",
            "labels/instance0.geojson",
        ),
        (
            "reference.tif",
            "images/frame0.tif",
            "images/frame1.tif",
            "images/frame2.tif",
            "labels/mask.tif",
            "labels/instance0.geojson",
            "labels/instance1.geojson",
        ),
    )
    samples = []
    for index, paths in enumerate(files):
        assets = [asset("mixed", index, "reference.tif", node=NodeInfo(kind="reference"))]
        assets.extend(asset("mixed", index, path) for path in paths if path.startswith("images/"))
        assets.append(asset("mixed", index, "labels/mask.tif"))
        assets.extend(
            asset("mixed", index, path, vector=VectorInfo(geometry_type="Polygon"))
            for path in paths
            if path.endswith(".geojson")
        )
        samples.append(
            taco.Sample(
                id=f"s{index}",
                metadata=taco.Metadata(core=SampleInfo(name=f"mixed-{index}")),
                folders=[
                    taco.Folder("images", metadata=taco.Metadata(node=NodeInfo(kind="images"))),
                    taco.Folder("labels", metadata=taco.Metadata(node=NodeInfo(kind="labels"))),
                ],
                assets=assets,
            )
        )
    metadata = taco.CollectionMetadata(benchmark=Benchmark(source="synthetic", revision=1))
    return DatasetCase(
        "mixed_structure",
        collection("mixed-structure", contract, metadata=metadata),
        tuple(samples),
        contract.levels,
        files,
        (2, 6, 5, 5),
    )


def deep_hierarchy() -> DatasetCase:
    paths = (
        "inputs/optical/B02.tif",
        "inputs/optical/B03.tif",
        "inputs/radar/VV.tif",
        "inputs/radar/VH.tif",
        "targets/segmentation/mask.tif",
    )
    contract = taco.Contract(
        structure=paths,
        metadata=[
            taco.Level("sample", istac=taco.extensions.ISTAC()),
            taco.Level("children", istac=taco.metadata.folder.ISTAC),
            taco.Level("children/inputs", istac=taco.metadata.folder.ISTAC),
            taco.Level("children/targets", istac=taco.metadata.folder.ISTAC),
        ],
    )
    samples = []
    for index in range(2):
        samples.append(
            taco.Sample(
                id=f"s{index}",
                metadata=taco.Metadata(istac=stac(index, taco.metadata.sample.ISTAC)),
                folders=[
                    taco.Folder("inputs", metadata=taco.Metadata(istac=stac(index, taco.metadata.folder.ISTAC))),
                    taco.Folder("targets", metadata=taco.Metadata(istac=stac(index, taco.metadata.folder.ISTAC))),
                    taco.Folder(
                        "inputs/optical",
                        metadata=taco.Metadata(istac=stac(index, taco.metadata.folder.ISTAC)),
                    ),
                    taco.Folder(
                        "inputs/radar",
                        metadata=taco.Metadata(istac=stac(index, taco.metadata.folder.ISTAC)),
                    ),
                    taco.Folder(
                        "targets/segmentation",
                        metadata=taco.Metadata(istac=stac(index, taco.metadata.folder.ISTAC)),
                    ),
                ],
                assets=[asset("deep", index, path) for path in paths],
            )
        )
    return DatasetCase(
        "deep_hierarchy",
        collection("deep-hierarchy", contract),
        tuple(samples),
        contract.levels,
        (paths, paths),
        (2, 4, 4, 2, 4, 4, 2),
    )


def rich_metadata() -> DatasetCase:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[
            taco.Level("sample", rich=RichMetadata),
            taco.Level("children", blob=BlobInfo),
        ],
    )
    samples = tuple(
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(
                rich=RichMetadata(
                    flag=index == 0,
                    payload=f"payload-{index}".encode(),
                    timestamp=datetime(2024, 2, index + 1, tzinfo=timezone.utc),
                    day=date(2024, 2, index + 1),
                    amount=Decimal(f"{index + 1}.25"),
                    tags=["one", str(index)],
                    shape=(32, 64),
                    detail=RichDetail(name=f"detail-{index}", score=None if index == 0 else 0.5),
                    lookup={"x": index, "y": index + 1},
                    optional=None if index == 0 else 1.5,
                )
            ),
            assets=[asset("rich", index, "data.bin", blob=BlobInfo(checksum=f"hash-{index}".encode()))],
        )
        for index in range(2)
    )
    return DatasetCase(
        "rich_metadata",
        collection("rich-metadata", contract),
        samples,
        contract.levels,
        (("data.bin",),) * 2,
        (2, 2),
    )


def derived_metadata() -> DatasetCase:
    contract = taco.Contract(
        structure=["image.tif", "label.tif"],
        metadata=[
            taco.Level(
                "sample",
                stac=taco.extensions.STAC(model=CloudSTAC),
                ml=taco.metadata.sample.Split,
                majortom=taco.extensions.MajorTOM(dist_km=100),
            ),
            taco.Level(
                "children",
                scaling=taco.metadata.asset.Scaling,
            ),
        ],
    )
    splits = ("train", "validation", "test")
    samples = []
    for index, split in enumerate(splits):
        metadata = stac(index, CloudSTAC, cloud_cover=index * 12.5)
        assets = []
        for path in ("image.tif", "label.tif"):
            assets.append(
                asset(
                    "derived",
                    index,
                    path,
                    scaling=taco.metadata.asset.Scaling(scale_factor=0.01, scale_offset=0),
                )
            )
        samples.append(
            taco.Sample(
                id=f"s{index}",
                metadata=taco.Metadata(
                    stac=metadata,
                    ml=taco.metadata.sample.Split(split=split),
                ),
                assets=assets,
            )
        )
    collection_metadata = taco.CollectionMetadata(
        labels=taco.metadata.collection.Labels(classes=["clear", "cloud"]),
        optical=taco.metadata.collection.Optical(
            sensor="synthetic",
            bands=[taco.metadata.collection.SpectralBand(name="B02", index=0)],
        ),
        scientific=taco.metadata.collection.Publications(
            publications=[
                taco.metadata.collection.Publication(
                    doi="10.0000/taco.test",
                    citation="TACO writer regression dataset",
                )
            ]
        ),
        split=taco.metadata.collection.SplitStrategy(strategy="manual"),
    )
    files = (("image.tif", "label.tif"),) * len(samples)
    return DatasetCase(
        "derived_metadata",
        collection("derived-metadata", contract, metadata=collection_metadata),
        tuple(samples),
        contract.levels,
        files,
        (3, 6),
    )


def independent_profile(profile: str) -> DatasetCase:
    sample_extension = {
        "spatial": taco.extensions.Spatial,
        "ispatial": taco.extensions.ISpatial,
        "temporal": taco.extensions.Temporal,
    }[profile]
    folder_model = {
        "spatial": taco.metadata.folder.Spatial,
        "ispatial": taco.metadata.folder.ISpatial,
        "temporal": taco.metadata.folder.Temporal,
    }[profile]
    contract = taco.Contract(
        structure=["scene/data.bin"],
        metadata=[
            taco.Level("sample", **{profile: sample_extension()}),
            taco.Level("children", **{profile: sample_extension(model=folder_model)}),
        ],
    )
    samples = tuple(
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(**{profile: spatial_profile(profile, index, sample_extension().input_model)}),
            folders=[
                taco.Folder(
                    "scene",
                    metadata=taco.Metadata(**{profile: spatial_profile(profile, index, folder_model)}),
                )
            ],
            assets=[asset(profile, index, "scene/data.bin")],
        )
        for index in range(2)
    )
    return DatasetCase(
        f"{profile}_profile",
        collection(f"{profile}-profile", contract),
        samples,
        contract.levels,
        (("scene/data.bin",),) * 2,
        (2, 2, 2),
    )


CASES = (
    single_file(),
    flat_assets(),
    nested_folders(),
    variable_sequence(),
    mixed_structure(),
    deep_hierarchy(),
    rich_metadata(),
    derived_metadata(),
    independent_profile("spatial"),
    independent_profile("ispatial"),
    independent_profile("temporal"),
)


def case_id(case: DatasetCase) -> str:
    return case.name


def get_case(name: str) -> DatasetCase:
    return next(case for case in CASES if case.name == name)
