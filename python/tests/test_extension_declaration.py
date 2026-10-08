from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel

import taco
from taco.errors import CollectionError, ContractError
from taco.writer.base import render_collection

BASE = "https://asterisk.coop/taco/spec/extensions"
STAC = f"{BASE}/stac/v1.0.0/schema.json"
MAJORTOM = f"{BASE}/majortom/v1.0.0/schema.json"
RUMI = f"{BASE}/rumi/v1.0.0/schema.json"


def edit_collection(path: Path, change: Callable[[dict[str, Any]], Any]) -> None:
    file = path / "COLLECTION.json"
    data = json.loads(file.read_text())
    change(data)
    file.write_text(json.dumps(data))


def extension_errors(path: Path) -> list[str]:
    return [issue.message for issue in taco.validate(path).errors if issue.code == "extensions"]


def test_writer_lists_the_extensions_it_uses(folder_dataset: Path) -> None:
    stored = json.loads((folder_dataset / "COLLECTION.json").read_text())
    assert stored["taco:extensions"] == [MAJORTOM, STAC]
    assert taco.open_dataset(folder_dataset).collection.extensions == (MAJORTOM, STAC)
    assert taco.validate(folder_dataset).ok


def test_collection_declares_builtins_before_writing(collection: taco.Collection) -> None:
    assert collection.extensions == (MAJORTOM, STAC)
    assert collection.to_dict()["taco:extensions"] == [MAJORTOM, STAC]


def test_collection_accepts_a_third_party_extension(tmp_path: Path) -> None:
    class Quality(BaseModel):
        score: float

    identifier = "https://example.com/quality/v1.0.0/schema.json"
    collection = taco.Collection(
        contract=taco.Contract(structure=["a.bin"], metadata=[taco.Level("sample", quality=Quality)]),
        id="quality",
        description="Quality scores",
        licenses=["MIT"],
        providers=["me"],
        extensions=[identifier],
    )
    assert collection.extensions == (identifier,)
    assert json.loads(render_collection(collection, {}))["taco:extensions"] == [identifier]
    path = tmp_path / "quality.zip"
    with taco.open_writer(collection, path) as writer:
        writer.add(taco.Sample(id="a", assets=b"x", metadata=taco.Metadata(quality=Quality(score=1.0))))
        writer.run()
    assert taco.open_dataset(path).collection.extensions == (identifier,)
    assert taco.validate(path).ok


def test_writer_preserves_a_declared_builtin_version(collection: taco.Collection) -> None:
    future = f"{BASE}/stac/v1.1.0/schema.json"
    loaded = taco.Collection.from_dict({**collection.to_dict(), "taco:extensions": [MAJORTOM, future]})
    stored = json.loads(render_collection(loaded, {}))
    assert stored["taco:extensions"] == [MAJORTOM, future]


def test_a_dataset_without_extensions_lists_none(tmp_path: Path) -> None:
    collection = taco.Collection(
        contract=taco.Contract(structure=["a.bin"]),
        id="plain",
        description="No extension",
        licenses=["MIT"],
        providers=["me"],
    )
    with taco.open_writer(collection, tmp_path / "plain.zip") as writer:
        writer.add(taco.Sample(id="a", assets=b"x"))
        writer.run()
    assert taco.open_dataset(tmp_path / "plain.zip").collection.extensions == ()
    assert taco.validate(tmp_path / "plain.zip").ok


def test_a_missing_list_still_reads_but_does_not_validate(folder_dataset: Path) -> None:
    edit_collection(folder_dataset, lambda data: data.pop("taco:extensions"))
    assert taco.open_dataset(folder_dataset).read().num_rows == 4
    assert any("must list the extensions" in message for message in extension_errors(folder_dataset))


def test_a_namespace_in_use_needs_its_extension(folder_dataset: Path) -> None:
    edit_collection(folder_dataset, lambda data: data["taco:extensions"].remove(STAC))
    errors = extension_errors(folder_dataset)
    assert any(message.startswith("stac fields need") for message in errors)
    assert any("breaks" in message and "majortom" in message for message in errors)


def test_two_versions_of_one_extension_are_rejected(folder_dataset: Path) -> None:
    edit_collection(folder_dataset, lambda data: data["taco:extensions"].append(f"{BASE}/stac/v1.1.0/schema.json"))
    report = taco.validate(folder_dataset)
    assert any("lists stac more than once" in issue.message for issue in report.errors)
    assert any(issue.severity == "warning" and "v1.1.0" in issue.message for issue in report.issues)


def test_an_unknown_extension_is_only_a_warning(folder_dataset: Path) -> None:
    edit_collection(
        folder_dataset, lambda data: data["taco:extensions"].append("https://example.com/ext/v1/schema.json")
    )
    report = taco.validate(folder_dataset)
    assert report.ok
    assert [issue.code for issue in report.warnings] == ["extensions"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda data: data.update({"majortom:dist_km": 0}), "majortom:dist_km breaks"),
        (lambda data: data.update({"majortom:unknown": 1}), "breaks"),
        (lambda data: data.update({"majortom:extra": {"fine": 10}}), "do not match majortom:extra"),
        (lambda data: data.update({"majortom:latitude_range": [10, -10]}), "latitude_range must increase"),
        (lambda data: data.update({"majortom:longitude_range": [0, 0]}), "longitude_range must increase"),
    ],
)
def test_majortom_collection_fields_are_checked(
    folder_dataset: Path, change: Callable[[dict[str, Any]], Any], expected: str
) -> None:
    edit_collection(folder_dataset, change)
    assert any(expected in message for message in extension_errors(folder_dataset))


def test_one_profile_class_describes_folders_and_files(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["x/y.tif", "z.tif"],
        metadata=[taco.Level("children", stac=taco.extensions.sample.stac.STAC | None)],
    )
    collection = taco.Collection(contract=contract, id="rows", description="Rows", licenses=["MIT"], providers=["me"])
    grid = taco.extensions.sample.stac.STAC(
        proj_code="EPSG:4326",
        proj_shape=(2, 2),
        proj_transform=(0.1, 0, -76, 0, -0.1, -12),
        datetime="2024-01-01T00:00:00Z",
    )
    path = tmp_path / "rows"
    with taco.open_writer(collection, path) as writer:
        writer.add(
            taco.Sample(
                id="a",
                folders=[taco.Folder("x", metadata=taco.Metadata(stac=grid))],
                assets=[
                    taco.Asset(b"y", path="x/y.tif"),
                    taco.Asset(b"z", path="z.tif", metadata=taco.Metadata(stac=grid)),
                ],
            )
        )
        writer.run()
    centroids = pq.read_table(path / "METADATA" / "children.parquet").column("stac:centroid").to_pylist()
    assert centroids[0] == centroids[1] is not None
    assert taco.validate(path).ok


def test_extra_fields_in_a_profile_namespace_are_rejected() -> None:
    class CloudSTAC(taco.extensions.sample.stac.STAC):
        cloud_cover: float

    with pytest.raises(ContractError, match="does not define \\['cloud_cover'\\]"):
        taco.Contract(structure=["a.tif"], metadata=[taco.Level("sample", stac=CloudSTAC)])


def test_extra_fields_in_a_profile_mapping_are_rejected() -> None:
    contract = taco.Contract(
        structure=["a.tif"], metadata=[taco.Level("sample", stac=taco.extensions.sample.stac.STAC)]
    )
    metadata = contract.to_dict()["taco:metadata"]
    metadata["sample"]["stac:cloud_cover"] = {"type": "double", "nullable": True, "description": ""}
    with pytest.raises(ContractError, match="does not define"):
        taco.Contract(structure=["a.tif"], metadata=metadata)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not a list", "list of strings"),
        ([STAC, STAC], "must not repeat"),
        ([""], "non-empty strings"),
        ([1], "non-empty strings"),
    ],
)
def test_collection_rejects_a_malformed_list(collection: taco.Collection, value: object, message: str) -> None:
    data = {**collection.to_dict(), "taco:extensions": value}
    with pytest.raises(CollectionError, match=message):
        taco.Collection.from_dict(data)


def test_replace_completes_the_list_and_resets_it_with_the_contract(collection: taco.Collection) -> None:
    loaded = taco.Collection.from_dict({**collection.to_dict(), "taco:extensions": [STAC]})
    assert loaded.replace(title="Renamed").extensions == (MAJORTOM, STAC)
    custom = "https://example.com/quality/v1.0.0/schema.json"
    assert loaded.replace(extensions=[custom]).extensions == (MAJORTOM, STAC, custom)
    changed = loaded.replace(contract=taco.Contract(structure=["a.bin"]))
    assert changed.extensions == ()
    assert "majortom" not in changed.metadata


def test_the_list_is_not_read_as_an_extension_descriptor(collection: taco.Collection) -> None:
    data = {**collection.to_dict(), "taco:extensions": [STAC]}
    assert taco.Contract.from_dict(data).operations == {}


def test_export_and_consolidation_keep_the_list(
    tmp_path: Path, collection: taco.Collection, make_sample, folder_dataset: Path
) -> None:
    exported = tmp_path / "subset.zip"
    taco.export(folder_dataset, exported, sql="SELECT * FROM sample WHERE id = 's0'")
    assert taco.open_dataset(exported).collection.extensions == (MAJORTOM, STAC)

    with taco.open_writer(collection, tmp_path / "parts.zip", partition_size=1) as writer:
        writer.extend(make_sample(index) for index in range(2))
        catalog = writer.run().path
    stored = json.loads((Path(catalog) / "COLLECTION.json").read_text())
    assert stored["taco:extensions"] == [MAJORTOM, STAC]
    assert taco.validate(catalog).ok


@pytest.mark.parametrize("namespace", ["stac", "spatial", "temporal", "rumi", "majortom", "geoenrich"])
def test_a_user_model_cannot_take_an_owned_namespace(namespace: str) -> None:
    class Grid(BaseModel):
        code: int

    for value in (Grid, Grid | None):
        with pytest.raises(ContractError, match=f"{namespace!r} is reserved for the"):
            taco.Level("sample", **{namespace: value})


def test_a_user_extension_cannot_take_an_owned_namespace() -> None:
    @dataclass(frozen=True)
    class Code(taco.Extension):
        @property
        def requires(self) -> tuple[str, ...]:
            return ()

        @property
        def fields(self) -> pa.Schema:
            return pa.schema([pa.field("code", pa.string())])

        def run(self, context: taco.ExtensionContext) -> dict[str, list[str]]:
            return {"code": ["x"] * len(context.assets)}

    with pytest.raises(ContractError, match="'majortom' is reserved for the majortom extension"):
        taco.Level("sample", majortom=Code())
    assert taco.Level("sample", code=Code()).groups
