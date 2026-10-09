"""ML validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import _entry_problems

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView


def _names(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(entry, str) for entry in value)


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate ml entries against the contract."""
    document = dataset.collection_json
    inputs = document.get("ml:inputs", [])
    targets = document.get("ml:targets", [])
    # The schema reports entries of the wrong shape.
    if not _names(inputs) or not _names(targets):
        return []
    issues = [("extensions", problem) for problem in _entry_problems(inputs, targets, dataset.contract)]
    both = sorted(set(inputs) & set(targets))
    if both:
        issues.append(("extensions", f"{both} cannot be both an input and a target"))
    return issues
