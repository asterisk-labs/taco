from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_serializer, model_validator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....contract.contract import Contract

_RECORD_FIELDS = ("name", "acronym", "type", "platform", "operator")
_Name = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


@cache
def _catalogue() -> dict[str, Any]:
    """Load the vendored AEOI catalogue."""
    text = files("taco.extensions._builtin.instrument").joinpath("aeoi", "catalogue.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _catalogue_pointer() -> dict[str, str]:
    data = _catalogue()
    return {"name": data["name"], "version": data["version"], "link": data["link"]}


def _aeoi_record(identifier: str) -> dict[str, Any]:
    entry = _catalogue()["instruments"][identifier]
    record = {name: entry[name] for name in _RECORD_FIELDS if name in entry}
    record["bands"] = entry.get("extensions", {}).get("spectral", {}).get("bands") or {}
    return record


class _BandRecord(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    center_wavelength: float = Field(gt=0, description="Center wavelength in nm")
    bandwidth: float = Field(gt=0, description="Bandwidth in nm")


class _InstrumentRecord(BaseModel):
    """An instrument missing from AEOI."""

    model_config = ConfigDict(extra="allow", frozen=True)

    name: _Name
    type: _Name
    bands: dict[_Name, _BandRecord] = Field(min_length=1)


class _FileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruments: list[_Name] = Field(min_length=1)
    bands: list[_Name] = Field(min_length=1)

    @field_validator("instruments", "bands")
    @classmethod
    def _distinct(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("must not repeat a name")
        return values


class Instrument(BaseModel):
    """Instruments and bands used by each structure entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"collection"})
    __taco_namespace__: ClassVar[str | None] = "instrument"

    files: dict[_Name, _FileRecord] = Field(min_length=1)
    instruments: dict[_Name, _InstrumentRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known(self) -> Instrument:
        known = _catalogue()["instruments"]
        version = _catalogue()["version"]
        clashes = sorted(set(self.instruments) & set(known))
        if clashes:
            raise ValueError(f"AEOI {version} already includes {clashes}")
        for path, file in self.files.items():
            for identifier in file.instruments:
                if identifier in self.instruments:
                    bands = set(self.instruments[identifier].bands)
                elif identifier in known:
                    bands = set(_aeoi_record(identifier)["bands"])
                else:
                    raise ValueError(f"{identifier!r} used by {path!r} is not in AEOI {version}. Add it to instruments")
                missing = [band for band in file.bands if band not in bands]
                if missing:
                    raise ValueError(f"{path!r} lists bands {missing} that {identifier!r} does not have")
        return self

    def __taco_check_contract__(self, contract: Contract) -> None:
        unknown = sorted(set(self.files) - set(contract.structure))
        if unknown:
            raise ValueError(f"instrument files are not in taco:structure {unknown}")

    @model_serializer(mode="plain")
    def _resolved(self) -> dict[str, Any]:
        used: dict[str, set[str]] = {}
        for file in self.files.values():
            for identifier in file.instruments:
                used.setdefault(identifier, set()).update(file.bands)
        records = {}
        for identifier in sorted(used):
            if identifier in self.instruments:
                record = self.instruments[identifier].model_dump(mode="json")
            else:
                record = _aeoi_record(identifier)
            record["bands"] = {name: value for name, value in record["bands"].items() if name in used[identifier]}
            records[identifier] = record
        return {
            "files": {path: file.model_dump(mode="json") for path, file in self.files.items()},
            "instruments": records,
            "catalogue": _catalogue_pointer(),
        }


__all__ = ["Instrument"]
