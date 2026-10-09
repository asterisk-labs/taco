from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import taco
from taco.errors import CollectionError
from taco.extensions.collection.labels import Labels

MASK = {0: "clear", 1: "thick_cloud", 10: "shadow", 255: "unlabeled"}


def collection(structure: list[str] | None = None, **labels: Any) -> taco.Collection:
    return taco.Collection(
        contract=taco.Contract(structure=structure or ["image.tif", "mask.tif"]),
        id="labels",
        description="Labels",
        licenses=["MIT"],
        providers=["me"],
        labels=Labels(**labels),
    )


def write(tmp_path: Path, value: taco.Collection) -> Path:
    path = tmp_path / "dataset"
    with taco.open_writer(value, path) as writer:
        writer.add(taco.Sample(id="a", assets=[taco.Asset(b"x", path=name) for name in value.contract.structure]))
        writer.run()
    return path


def stored(path: Path) -> Any:
    return json.loads((path / "COLLECTION.json").read_text())["labels:files"]


def test_values_keep_their_codes_in_numeric_order(tmp_path: Path) -> None:
    path = write(
        tmp_path, collection(files={"mask.tif": {255: "unlabeled", 10: "shadow", 0: "clear", 1: "thick_cloud"}})
    )
    labels = stored(path)["mask.tif"]
    assert labels == {"0": "clear", "1": "thick_cloud", "10": "shadow", "255": "unlabeled"}
    assert list(labels) == ["0", "1", "10", "255"]
    assert taco.validate(path).ok


def test_labels_read_back_into_the_model(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"mask.tif": MASK}))
    loaded = taco.open_dataset(path).collection.metadata["labels"]
    assert Labels(**loaded).files == {"mask.tif": MASK}


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "at least 1 item"),
        ({"mask.tif": {}}, "must name at least one value"),
        ({"mask.tif": {-1: "nodata"}}, "non-negative integer codes"),
        ({"mask.tif": {0: "  "}}, "non-empty names"),
        ({"mask.tif": {0: "cloud", 1: "cloud"}}, "must not repeat a name"),
        ({"mask.tif": {"water": "cloud"}}, "non-negative integer codes"),
        ({"mask.tif": {"01": "cloud"}}, "non-negative integer codes"),
        ({"mask.tif": {1.0: "cloud"}}, "non-negative integer codes"),
        ({"mask.tif": {True: "cloud"}}, "non-negative integer codes"),
        ({"mask.tif": {1: "clear", "1": "cloud"}}, "must not repeat a code"),
    ],
)
def test_invalid_labels_fail_when_described(files: dict[Any, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        Labels(files=files)


def test_labels_files_must_be_in_the_structure() -> None:
    with pytest.raises(CollectionError, match=r"\['label.tif'\] are not in taco:structure"):
        collection(files={"label.tif": MASK})


def test_replacing_the_contract_drops_labels(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"mask.tif": MASK}))
    changed = taco.open_dataset(path).collection.replace(contract=taco.Contract(structure=["mask.tif"]))
    assert "labels" not in changed.metadata
    assert not changed.extensions


def test_export_keeps_labels(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"mask.tif": MASK}))
    exported = tmp_path / "subset"
    taco.export(path, exported, sql="SELECT * FROM sample")
    assert stored(exported) == stored(path)
    assert taco.validate(exported).ok


def test_validate_checks_files_and_names(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"mask.tif": MASK}))
    file = path / "COLLECTION.json"
    document = json.loads(file.read_text())
    document["labels:files"]["gone.tif"] = {"0": "clear"}
    document["labels:files"]["mask.tif"] = {"0": "cloud", "1": "cloud"}
    file.write_text(json.dumps(document))
    messages = [issue.message for issue in taco.validate(path).errors if issue.code == "extensions"]
    assert any("['gone.tif'] are not in taco:structure" in message for message in messages)
    assert any("labels of 'mask.tif' must not repeat a name" in message for message in messages)


@pytest.mark.parametrize("value", ["bad", [], 3, {"0": []}, {"0": {"name": "clear"}}])
def test_validate_handles_malformed_labels(tmp_path: Path, value: Any) -> None:
    path = write(tmp_path, collection(files={"mask.tif": MASK}))
    file = path / "COLLECTION.json"
    document = json.loads(file.read_text())
    document["labels:files"]["mask.tif"] = value
    file.write_text(json.dumps(document))
    assert any(issue.code == "extensions" for issue in taco.validate(path).errors)
