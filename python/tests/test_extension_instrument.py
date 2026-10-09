from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import taco
from taco.errors import CollectionError
from taco.extensions._builtin.instrument.models import _catalogue
from taco.extensions.collection.instrument import Instrument

S2 = {"instruments": ["MSI_S2A", "MSI_S2B"], "bands": ["B2", "B4"]}
CAMERA = {"name": "My camera", "type": "multispectral", "bands": {"B1": {"center_wavelength": 560, "bandwidth": 40}}}


def collection(structure: list[str] | None = None, **instrument: Any) -> taco.Collection:
    return taco.Collection(
        contract=taco.Contract(structure=structure or ["s2.tif", "cam.tif", "mask.tif"]),
        id="instruments",
        description="Instruments",
        licenses=["MIT"],
        providers=["me"],
        instrument=Instrument(**instrument),
    )


def write(tmp_path: Path, value: taco.Collection) -> Path:
    path = tmp_path / "dataset"
    with taco.open_writer(value, path) as writer:
        writer.add(taco.Sample(id="a", assets=[taco.Asset(b"x", path=name) for name in value.contract.structure]))
        writer.run()
    return path


def stored(path: Path) -> dict[str, Any]:
    document = json.loads((path / "COLLECTION.json").read_text())
    return {key: value for key, value in document.items() if key.startswith("instrument:")}


def test_records_come_from_aeoi_with_only_the_used_bands(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"s2.tif": S2}))
    values = stored(path)
    assert values["instrument:files"] == {"s2.tif": S2}
    assert sorted(values["instrument:instruments"]) == ["MSI_S2A", "MSI_S2B"]
    s2a, s2b = values["instrument:instruments"]["MSI_S2A"], values["instrument:instruments"]["MSI_S2B"]
    assert sorted(s2a["bands"]) == ["B2", "B4"]
    aeoi = _catalogue()["instruments"]
    assert s2a["bands"]["B4"] == aeoi["MSI_S2A"]["extensions"]["spectral"]["bands"]["B4"]
    assert (
        s2b["bands"]["B4"]["center_wavelength"]
        == aeoi["MSI_S2B"]["extensions"]["spectral"]["bands"]["B4"]["center_wavelength"]
    )
    assert s2a["platform"] == ["Sentinel-2A"]
    assert values["instrument:catalogue"] == {key: _catalogue()[key] for key in ("name", "version", "link")}
    assert taco.validate(path).ok


def test_an_instrument_outside_aeoi_is_stored_as_described(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        collection(
            files={"cam.tif": {"instruments": ["MY_CAMERA"], "bands": ["B1"]}}, instruments={"MY_CAMERA": CAMERA}
        ),
    )
    record = stored(path)["instrument:instruments"]["MY_CAMERA"]
    assert record["name"] == "My camera"
    assert record["bands"] == {"B1": {"center_wavelength": 560.0, "bandwidth": 40.0}}
    assert taco.validate(path).ok


@pytest.mark.parametrize(
    ("instrument", "message"),
    [
        ({"files": {"s2.tif": {"instruments": ["MSI_S2A"], "bands": ["B13"]}}}, "does not have"),
        ({"files": {"s2.tif": {"instruments": ["MSI_S2A", "OLI_L8"], "bands": ["B8A"]}}}, "does not have"),
        ({"files": {"s2.tif": {"instruments": ["NOPE"], "bands": ["B1"]}}}, "not in AEOI"),
        (
            {"files": {"s2.tif": {"instruments": ["SEQUOIA_RGB_PARROT"], "bands": ["B1"]}}},
            "does not have",
        ),
        ({"files": {"s2.tif": S2}, "instruments": {"MSI_S2A": CAMERA}}, "already includes"),
        ({"files": {"s2.tif": {"instruments": ["MSI_S2A"], "bands": ["B2", "B2"]}}}, "must not repeat"),
        ({"files": {"s2.tif": {"instruments": [], "bands": ["B2"]}}}, "at least 1 item"),
        ({"files": {"s2.tif": {"instruments": ["MSI_S2A"], "bands": []}}}, "at least 1 item"),
        ({"files": {"": {"instruments": ["MSI_S2A"], "bands": ["B2"]}}}, "at least 1 character"),
        ({"files": {"s2.tif": {"instruments": [""], "bands": ["B2"]}}}, "at least 1 character"),
        ({"files": {}}, "at least 1 item"),
        ({"files": {"s2.tif": {**S2, "nodata": 0}}}, "Extra inputs"),
        (
            {
                "files": {"cam.tif": {"instruments": ["MY_CAMERA"], "bands": ["B1"]}},
                "instruments": {"MY_CAMERA": {"name": "x", "type": "x", "bands": {"B1": {"center_wavelength": 560}}}},
            },
            "bandwidth",
        ),
        (
            {
                "files": {"cam.tif": {"instruments": ["MY_CAMERA"], "bands": ["B1"]}},
                "instruments": {"MY_CAMERA": {**CAMERA, "bands": {"B1": {"center_wavelength": -1, "bandwidth": 40}}}},
            },
            "greater than 0",
        ),
    ],
)
def test_invalid_instruments_fail_when_described(instrument: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        Instrument(**instrument)


def test_files_must_be_in_the_structure() -> None:
    with pytest.raises(CollectionError, match=r"not in taco:structure \['s3.tif'\]"):
        collection(files={"s3.tif": S2})


def test_the_group_survives_rewrites(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"s2.tif": S2}))
    loaded = taco.open_dataset(path).collection
    assert loaded.metadata["instrument"] == {key.partition(":")[2]: value for key, value in stored(path).items()}
    assert loaded.replace(title="Renamed").metadata["instrument"] == loaded.metadata["instrument"]
    exported = tmp_path / "subset"
    taco.export(path, exported, sql="SELECT * FROM sample")
    assert stored(exported) == stored(path)
    assert taco.validate(exported).ok


def test_replacing_the_contract_drops_instrument_metadata(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"s2.tif": S2}))
    loaded = taco.open_dataset(path).collection
    changed = loaded.replace(contract=taco.Contract(structure=["mask.tif"]))
    assert "instrument" not in changed.metadata
    assert not changed.extensions


def test_validate_checks_files_and_records(tmp_path: Path) -> None:
    path = write(tmp_path, collection(files={"s2.tif": S2}))
    file = path / "COLLECTION.json"
    document = json.loads(file.read_text())
    document["instrument:files"]["gone.tif"] = S2
    document["instrument:files"]["s2.tif"] = {"instruments": ["MSI_S2A", "MSI_S2C"], "bands": ["B2", "B8"]}
    file.write_text(json.dumps(document))
    messages = [issue.message for issue in taco.validate(path).errors if issue.code == "extensions"]
    assert any("not in taco:structure ['gone.tif']" in message for message in messages)
    assert any("'MSI_S2C' used by 's2.tif' has no instrument record" in message for message in messages)
    assert any("'s2.tif' lists bands ['B8'] that 'MSI_S2A' does not have" in message for message in messages)


@pytest.mark.parametrize("value", ["bad", [], 3, {"instruments": [[]], "bands": ["B2"]}])
def test_validate_handles_malformed_file_records(tmp_path: Path, value: Any) -> None:
    path = write(tmp_path, collection(files={"s2.tif": S2}))
    file = path / "COLLECTION.json"
    document = json.loads(file.read_text())
    document["instrument:files"]["s2.tif"] = value
    file.write_text(json.dumps(document))
    issues = taco.validate(path).errors
    assert any(issue.code == "extensions" for issue in issues)


def test_a_plain_mapping_is_not_resolved(tmp_path: Path) -> None:
    value = taco.Collection(
        contract=taco.Contract(structure=["s2.tif"]),
        id="raw",
        description="Raw",
        licenses=["MIT"],
        providers=["me"],
        instrument={"files": {"s2.tif": S2}},
    )
    path = write(tmp_path, value)
    assert any("instrument:instruments" in issue.message for issue in taco.validate(path).errors)


def test_the_vendored_catalogue_matches_its_readme() -> None:
    readme = (Path(taco.__file__).parent / "extensions/_builtin/instrument/aeoi/README.md").read_text()
    assert f"- Version: {_catalogue()['version']}" in readme
    assert (
        (Path(taco.__file__).parent / "extensions/_builtin/instrument/aeoi/LICENSE")
        .read_text()
        .startswith("MIT License")
    )
