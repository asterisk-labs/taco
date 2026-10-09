from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....contract.contract import Contract


def _entry_problems(inputs: list[str], targets: list[str], contract: Contract) -> list[str]:
    """Return invalid or ambiguous entries."""
    fields: dict[str, list[str]] = {}
    for level, names in contract.metadata.items():
        for name in names:
            fields.setdefault(name, []).append(level)
    problems = []
    for entry in dict.fromkeys((*inputs, *targets)):
        # A field always has one ':'; a structure path never does.
        if ":" in entry:
            if entry not in fields:
                problems.append(f"ml entry {entry!r} is not a metadata field")
            elif len(fields[entry]) > 1:
                problems.append(f"ml entry {entry!r} appears at more than one level {fields[entry]}")
        elif entry not in contract.structure:
            problems.append(f"ml entry {entry!r} is not in taco:structure")
    return problems


class ML(BaseModel):
    """What a model reads and what it predicts.

    Each entry is a structure declaration, such as ``"s2.tif"``, or a metadata
    field, such as ``"scene:category"``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"collection"})
    __taco_namespace__: ClassVar[str | None] = "ml"

    inputs: list[str] = Field(default_factory=list)
    targets: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _entries(self) -> ML:
        entries = [*self.inputs, *self.targets]
        if any(not entry.strip() for entry in entries):
            raise ValueError("ml entries must not be empty")
        if len(set(self.inputs)) != len(self.inputs) or len(set(self.targets)) != len(self.targets):
            raise ValueError("ml must not repeat an entry")
        both = sorted(set(self.inputs) & set(self.targets))
        if both:
            raise ValueError(f"{both} cannot be both an input and a target")
        return self

    def __taco_check_contract__(self, contract: Contract) -> None:
        problems = _entry_problems(self.inputs, self.targets, contract)
        if problems:
            raise ValueError("; ".join(problems))


__all__ = ["ML"]
