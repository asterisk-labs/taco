from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import taco
from taco.errors import CollectionError
from taco.extensions.collection.ml import ML


class Scene(BaseModel):
    category: str


def contract() -> taco.Contract:
    return taco.Contract(structure=["s2.tif", "mask.tif"], metadata=[taco.Level("sample", scene=Scene)])


def collection(**ml: Any) -> taco.Collection:
    return taco.Collection(
        contract=contract(), id="ml", description="ML", licenses=["MIT"], providers=["me"], ml=ML(**ml)
    )


def write(tmp_path: Path, value: taco.Collection) -> Path:
    path = tmp_path / "dataset"
    with taco.open_writer(value, path) as writer:
        writer.add(
            taco.Sample(
                id="a",
                assets=[taco.Asset(b"x", path="s2.tif"), taco.Asset(b"y", path="mask.tif")],
                metadata=taco.Metadata(scene=Scene(category="forest")),
            )
        )
        writer.run()
    return path


def stored(path: Path) -> dict[str, Any]:
    document = json.loads((path / "COLLECTION.json").read_text())
    return {key: value for key, value in document.items() if key.startswith("ml:")}


@pytest.mark.parametrize(
    ("ml", "expected"),
    [
        ({"inputs": ["s2.tif"], "targets": ["mask.tif"]}, {"ml:inputs": ["s2.tif"], "ml:targets": ["mask.tif"]}),
        (
            {"inputs": ["s2.tif"], "targets": ["scene:category"]},
            {"ml:inputs": ["s2.tif"], "ml:targets": ["scene:category"]},
        ),
        ({"targets": ["mask.tif"]}, {"ml:inputs": [], "ml:targets": ["mask.tif"]}),
    ],
)
def test_entries_are_files_or_fields(tmp_path: Path, ml: dict[str, Any], expected: dict[str, Any]) -> None:
    path = write(tmp_path, collection(**ml))
    assert stored(path) == expected
    assert taco.validate(path).ok


@pytest.mark.parametrize(
    ("ml", "message"),
    [
        ({"targets": []}, "at least 1 item"),
        ({"inputs": ["s2.tif"], "targets": ["s2.tif"]}, "['s2.tif'] cannot be both an input and a target"),
        ({"targets": ["mask.tif", "mask.tif"]}, "must not repeat an entry"),
        ({"inputs": ["s2.tif", "s2.tif"], "targets": ["mask.tif"]}, "must not repeat an entry"),
        ({"targets": [" "]}, "must not be empty"),
        ({"targets": ["mask.tif"], "task": "segmentation"}, "Extra inputs"),
    ],
)
def test_invalid_ml_fails_when_described(ml: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        ML(**ml)


@pytest.mark.parametrize(
    ("ml", "message"),
    [
        ({"targets": ["label.tif"]}, "ml entry 'label.tif' is not in taco:structure"),
        ({"targets": ["scene:label"]}, "ml entry 'scene:label' is not a metadata field"),
        ({"inputs": ["s3.tif"], "targets": ["mask.tif"]}, "ml entry 's3.tif' is not in taco:structure"),
    ],
)
def test_entries_must_exist_in_the_contract(ml: dict[str, Any], message: str) -> None:
    with pytest.raises(CollectionError, match=re.escape(message)):
        collection(**ml)


def test_metadata_fields_must_identify_one_level() -> None:
    ambiguous = taco.Contract(
        structure=["s2.tif", "mask.tif"],
        metadata=[taco.Level("sample", scene=Scene), taco.Level("children", scene=Scene)],
    )
    with pytest.raises(CollectionError, match="appears at more than one level"):
        taco.Collection(
            contract=ambiguous,
            id="ml",
            description="ML",
            licenses=["MIT"],
            providers=["me"],
            ml=ML(targets=["scene:category"]),
        )


def test_validate_checks_stored_entries(tmp_path: Path) -> None:
    path = write(tmp_path, collection(inputs=["s2.tif"], targets=["mask.tif"]))
    file = path / "COLLECTION.json"
    document = json.loads(file.read_text())
    document["ml:inputs"] = ["s2.tif", "gone.tif", "mask.tif"]
    document["ml:targets"] = ["mask.tif", "scene:label"]
    file.write_text(json.dumps(document))
    messages = [issue.message for issue in taco.validate(path).errors if issue.code == "extensions"]
    assert any("'gone.tif' is not in taco:structure" in message for message in messages)
    assert any("'scene:label' is not a metadata field" in message for message in messages)
    assert any("['mask.tif'] cannot be both an input and a target" in message for message in messages)


@pytest.mark.parametrize("value", ["s2.tif", [1], {"a": "b"}])
def test_validate_handles_malformed_entries(tmp_path: Path, value: Any) -> None:
    path = write(tmp_path, collection(inputs=["s2.tif"], targets=["mask.tif"]))
    file = path / "COLLECTION.json"
    document = json.loads(file.read_text())
    document["ml:inputs"] = value
    file.write_text(json.dumps(document))
    assert any(issue.code == "extensions" for issue in taco.validate(path).errors)


def test_export_keeps_ml(tmp_path: Path) -> None:
    path = write(tmp_path, collection(inputs=["s2.tif"], targets=["mask.tif"]))
    exported = tmp_path / "subset"
    taco.export(path, exported, sql="SELECT * FROM sample")
    assert stored(exported) == stored(path)
    assert taco.validate(exported).ok


def test_replacing_the_contract_drops_ml(tmp_path: Path) -> None:
    path = write(tmp_path, collection(inputs=["s2.tif"], targets=["mask.tif"]))
    changed = taco.open_dataset(path).collection.replace(contract=taco.Contract(structure=["s2.tif"]))
    assert "ml" not in changed.metadata
