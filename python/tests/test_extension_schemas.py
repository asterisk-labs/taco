from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import taco
from taco.contract.extension_registry import BUILTINS, declared
from taco.contract.extension_registry import schema as schema_of_package
from taco.writer.metadata import summary_types

jsonschema = pytest.importorskip("jsonschema")

EXTENSIONS = Path(__file__).resolve().parents[2] / "docs" / "spec" / "extensions"
BASE = "https://asterisk.coop/taco/spec/extensions"
NAMES = ["stac", "rumi", "majortom", "geoenrich", "split"]


def identifier(name: str) -> str:
    return f"{BASE}/{name}/v1.0.0/schema.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_of(url: str) -> dict[str, Any]:
    assert url.startswith(BASE + "/"), url
    return load(EXTENSIONS / url.removeprefix(BASE + "/"))


def errors(document: dict[str, Any]) -> list[str]:
    found = []
    for url in document.get("taco:extensions", []):
        validator = jsonschema.Draft202012Validator(schema_of(url))
        found += [f"{url}: {error.message}" for error in validator.iter_errors(document)]
    return found


def example(name: str) -> dict[str, Any]:
    return load(EXTENSIONS / name / "examples" / "COLLECTION.json")


def document(structure: list[str], levels: dict[str, dict[str, Any]], extensions: list[str]) -> dict[str, Any]:
    contract = taco.Contract(structure=structure, metadata=[taco.Level(k, **v) for k, v in levels.items()])
    collection = taco.Collection(
        contract=contract,
        id="schema-check",
        description="Schema check",
        licenses=["CC-BY-4.0"],
        providers=[{"name": "Asterisk Labs"}],
    )
    out = collection.to_dict()
    for level in levels.values():
        for namespace, group in level.items():
            if isinstance(group, taco.Extension):
                out |= {f"{namespace}:{key}": value for key, value in group.collection_metadata().items()}
    # The summaries the writer adds, as for a dataset without rows.
    for name, summary in summary_types(contract).items():
        value = summary.merge([])
        if value is not None:
            out[name] = value
    out["taco:extensions"] = declared(out)
    assert out["taco:extensions"] == sorted(identifier(name) for name in extensions)
    return out


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_valid_and_matches_its_identifier(name: str) -> None:
    schema = load(EXTENSIONS / name / "v1.0.0" / "schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"] == identifier(name)
    header = (EXTENSIONS / f"{name}.md").read_text(encoding="utf-8")
    assert re.search(rf"\*\*Identifier:\*\* `{re.escape(identifier(name))}`", header)


@pytest.mark.parametrize("name", NAMES)
def test_package_ships_the_published_schema(name: str) -> None:
    assert BUILTINS[name][0] == identifier(name)
    assert schema_of_package(identifier(name)) == load(EXTENSIONS / name / "v1.0.0" / "schema.json")


@pytest.mark.parametrize("name", NAMES)
def test_example_validates(name: str) -> None:
    document = example(name)
    assert identifier(name) in document["taco:extensions"]
    assert errors(document) == []


E = taco.extensions.sample
WRITER_CONTRACTS: dict[str, tuple[list[str], dict[str, dict[str, Any]], list[str]]] = {
    "temporal": (["a.tif"], {"sample": {"temporal": taco.extensions.sample.stac.Temporal}}, ["stac"]),
    "spatial": (["a.tif"], {"sample": {"spatial": E.stac.Spatial}}, ["stac"]),
    "optional stac": (["a.tif"], {"sample": {"stac": taco.extensions.sample.stac.STAC | None}}, ["stac"]),
    "folder stac": (["x/a.tif"], {"children": {"stac": taco.extensions.sample.stac.STAC}}, ["stac"]),
    "rumi header": (["a.rumi"], {"sample": {"rumi": E.rumi.Rumi()}}, ["rumi"]),
    "rumi stats": (["x/a.rumi"], {"children/x": {"rumi": E.rumi.Rumi(stats=True)}}, ["rumi"]),
    "rumi per file": (
        ["x/a.rumi", "x/b*[1,3].rumi"],
        {
            "children/x": {
                "rumi": E.rumi.Rumi(header=False, stats={"x/a.rumi": "mean_b2", "x/b*[1,3].rumi": ["p2_t0_b10"]})
            }
        },
        ["rumi"],
    ),
    "majortom": (
        ["a.tif"],
        {
            "sample": {
                "stac": E.stac.STAC,
                "majortom": E.majortom.MajorTOM(dist_km=50, extra={"fine": 1, "wide": 1000}, sep="-"),
            }
        },
        ["stac", "majortom"],
    ),
    "majortom on spatial": (
        ["a.tif"],
        {"sample": {"spatial": E.stac.Spatial, "majortom": E.majortom.MajorTOM(centroid="spatial:centroid")}},
        ["stac", "majortom"],
    ),
    "geoenrich index": (
        ["a.tif"],
        {
            "sample": {
                "stac": E.stac.STAC,
                "majortom": E.majortom.MajorTOM(dist_km=10),
                "geoenrich": E.geoenrich.GeoEnrich(),
            }
        },
        ["stac", "majortom", "geoenrich"],
    ),
    "geoenrich earthengine": (
        ["a.tif"],
        {"sample": {"stac": E.stac.STAC, "geoenrich": E.geoenrich.GeoEnrich(["elevation"], backend="earthengine")}},
        ["stac", "geoenrich"],
    ),
    "split": (["a.tif"], {"sample": {"split": E.split.Split}}, ["split"]),
    "split with stac": (["a.tif"], {"sample": {"stac": E.stac.STAC, "split": E.split.Split}}, ["split", "stac"]),
}


@pytest.mark.parametrize("case", sorted(WRITER_CONTRACTS))
def test_writer_contracts_validate(case: str) -> None:
    assert errors(document(*WRITER_CONTRACTS[case])) == []


def sample(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["taco:metadata"]["sample"]


def rumi_level(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["taco:metadata"]["children/rumi"]


def without_extension(name: str) -> Callable[[dict[str, Any]], None]:
    return lambda doc: doc["taco:extensions"].remove(identifier(name))


def drop(key: str) -> Callable[[dict[str, Any]], None]:
    return lambda doc: doc.pop(key)


INVALID: dict[str, tuple[str, Callable[[dict[str, Any]], Any]]] = {
    # STAC
    "stac not listed": ("majortom", without_extension("stac")),
    "unknown stac field": ("stac", lambda d: sample(d).update({"stac:cloud": sample(d)["stac:bbox"]})),
    "stac collection field": ("stac", lambda d: d.update({"stac:version": "1.1.0"})),
    "wrong centroid type": (
        "stac",
        lambda d: sample(d)["stac:centroid"].update(type="struct<lon: double, lat: double>"),
    ),
    "millisecond datetime": ("stac", lambda d: sample(d)["stac:datetime"].update(type="timestamp[ms, UTC]")),
    "required datetime": ("stac", lambda d: sample(d)["stac:datetime"].update(nullable=False)),
    "incomplete profile": ("stac", lambda d: sample(d).pop("stac:end_datetime")),
    "two profiles": (
        "stac",
        lambda d: sample(d).update(
            {f"temporal:{f}": sample(d)[f"stac:{f}"] for f in ("datetime", "start_datetime", "end_datetime")}
        ),
    ),
    "files on stac": ("stac", lambda d: sample(d)["stac:bbox"].update(files=["image.tif"])),
    "unknown declaration key": ("stac", lambda d: sample(d)["stac:bbox"].update(unit="degree")),
    "missing description": ("stac", lambda d: sample(d)["stac:bbox"].pop("description")),
    # Rumi
    "unknown statistic": ("rumi", lambda d: rumi_level(d).update({"rumi:median": rumi_level(d)["rumi:mean"]})),
    "leading zero band": ("rumi", lambda d: rumi_level(d).update({"rumi:mean_b01": rumi_level(d)["rumi:mean"]})),
    "band before time": ("rumi", lambda d: rumi_level(d).update({"rumi:mean_b1_t0": rumi_level(d)["rumi:mean"]})),
    "old stats field": ("rumi", lambda d: rumi_level(d).update({"rumi:stats": rumi_level(d)["rumi:mean"]})),
    "float statistic": ("rumi", lambda d: rumi_level(d)["rumi:mean"].update(type="float")),
    "required statistic": ("rumi", lambda d: rumi_level(d)["rumi:mean"].update(nullable=False)),
    "string header": ("rumi", lambda d: rumi_level(d)["rumi:header"].update(type="string")),
    "files on header": ("rumi", lambda d: rumi_level(d)["rumi:header"].update(files=["rumi/image.rumi"])),
    "empty files": ("rumi", lambda d: rumi_level(d)["rumi:mean"].update(files=[])),
    "repeated files": ("rumi", lambda d: rumi_level(d)["rumi:mean"].update(files=["a.rumi", "a.rumi"])),
    "rumi not listed": ("rumi", lambda d: d.update({"taco:extensions": []})),
    # MajorTOM
    "missing dist_km": ("majortom", drop("majortom:dist_km")),
    "zero dist_km": ("majortom", lambda d: d.update({"majortom:dist_km": 0})),
    "extra named code": ("majortom", lambda d: d.update({"majortom:extra": {"code": 10}})),
    "extra with colon": ("majortom", lambda d: d.update({"majortom:extra": {"a:b": 10}})),
    "negative extra": ("majortom", lambda d: d.update({"majortom:extra": {"fine": -1}})),
    "alphanumeric sep": ("majortom", lambda d: d.update({"majortom:sep": "x"})),
    "latitude out of range": ("majortom", lambda d: d.update({"majortom:latitude_range": [-95, 85]})),
    "centroid not a centroid": ("majortom", lambda d: d.update({"majortom:centroid": "stac:bbox"})),
    "unknown collection field": ("majortom", lambda d: d.update({"majortom:version": 2})),
    "nullable code": ("majortom", lambda d: sample(d)["majortom:code"].update(nullable=True)),
    "integer code": ("majortom", lambda d: sample(d)["majortom:code"].update(type="int64")),
    # GeoEnrich
    "unknown variable": ("geoenrich", lambda d: sample(d).update({"geoenrich:ndvi": sample(d)["geoenrich:elevation"]})),
    "double variable": ("geoenrich", lambda d: sample(d)["geoenrich:elevation"].update(type="double")),
    "nullable admin": ("geoenrich", lambda d: sample(d)["geoenrich:admin_countries"].update(nullable=True)),
    "backend without index": ("geoenrich", drop("geoenrich:index_url")),
    "index without backend": ("geoenrich", drop("geoenrich:backend")),
    "unknown backend": ("geoenrich", lambda d: d.update({"geoenrich:backend": "earthengine"})),
    "index without majortom": ("geoenrich", without_extension("majortom")),
    # Split
    "split not listed": ("split", without_extension("split")),
    "split collection field": ("split", lambda d: d.update({"split:strategy": "random"})),
    "unknown split field": ("split", lambda d: sample(d).update({"split:original": sample(d)["split:split"]})),
    "nullable split": ("split", lambda d: sample(d)["split:split"].update(nullable=True)),
    "integer split": ("split", lambda d: sample(d)["split:split"].update(type="int64")),
    "split below the sample": (
        "split",
        lambda d: d["taco:metadata"]["children"].update({"split:split": sample(d)["split:split"]}),
    ),
    "split missing": ("split", lambda d: sample(d).pop("split:split")),
    "counts missing": ("split", drop("split:counts")),
    "count missing": ("split", lambda d: d["split:counts"].pop("excluded")),
    "unknown count": ("split", lambda d: d["split:counts"].update(val=1)),
    "negative count": ("split", lambda d: d["split:counts"].update(test=-1)),
    "fractional count": ("split", lambda d: d["split:counts"].update(test=1.5)),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_collection_is_rejected(case: str) -> None:
    name, mutate = INVALID[case]
    doc = copy.deepcopy(example(name))
    mutate(doc)
    if case.endswith(" not listed") and not doc["taco:extensions"]:
        # Without its identifier no schema runs; the rule is that the schema requires it.
        schema = schema_of(identifier(name))
        assert list(jsonschema.Draft202012Validator(schema).iter_errors(doc))
        return
    assert errors(doc)


def test_earthengine_needs_stac_listed() -> None:
    doc = document(*WRITER_CONTRACTS["geoenrich earthengine"])
    doc["taco:extensions"].remove(identifier("stac"))
    assert errors(doc)


def test_user_namespaces_pass() -> None:
    doc = copy.deepcopy(example("stac"))
    sample(doc)["ml:split"] = {"type": "string", "nullable": False, "description": "Dataset split"}
    doc["poi:category"] = "volcano"
    assert errors(doc) == []
