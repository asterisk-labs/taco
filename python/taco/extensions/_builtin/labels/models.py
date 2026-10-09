from __future__ import annotations

import re
from collections.abc import Mapping
from numbers import Integral
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....contract.contract import Contract

_CODE = re.compile(r"0|[1-9][0-9]*")


def _parse_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Integral):
        parsed = int(value)
        return parsed if parsed >= 0 else None
    if isinstance(value, str) and _CODE.fullmatch(value):
        return int(value)
    return None


class Labels(BaseModel):
    """The class name of each value in a file.

    ``files`` maps a structure declaration to its values and their names, such
    as ``{"mask.tif": {0: "clear", 1: "cloud", 255: "unlabeled"}}``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"collection"})
    __taco_namespace__: ClassVar[str | None] = "labels"

    files: dict[str, dict[int, str]] = Field(min_length=1)

    @field_validator("files", mode="before")
    @classmethod
    def _codes(cls, files: Any) -> Any:
        if not isinstance(files, Mapping):
            return files
        normalized = {}
        for path, labels in files.items():
            if not isinstance(labels, Mapping):
                normalized[path] = labels
                continue
            values = {}
            for code, name in labels.items():
                parsed = _parse_code(code)
                if parsed is None:
                    raise ValueError(f"labels of {path!r} must use non-negative integer codes")
                if parsed in values:
                    raise ValueError(f"labels of {path!r} must not repeat a code")
                values[parsed] = name
            normalized[path] = values
        return normalized

    @field_validator("files")
    @classmethod
    def _names(cls, files: dict[str, dict[int, str]]) -> dict[str, dict[int, str]]:
        for path, labels in files.items():
            if not labels:
                raise ValueError(f"labels of {path!r} must name at least one value")
            if any(not name.strip() for name in labels.values()):
                raise ValueError(f"labels of {path!r} must use non-empty names")
            if len(set(labels.values())) != len(labels):
                raise ValueError(f"labels of {path!r} must not repeat a name")
        return files

    def __taco_check_contract__(self, contract: Contract) -> None:
        unknown = sorted(set(self.files) - set(contract.structure))
        if unknown:
            raise ValueError(f"labels files {unknown} are not in taco:structure")

    @model_serializer(mode="plain")
    def _stored(self) -> dict[str, Any]:
        # JSON keys are strings; keep the values in numeric order.
        return {
            "files": {
                path: {str(value): classes[value] for value in sorted(classes)} for path, classes in self.files.items()
            }
        }


__all__ = ["Labels"]
