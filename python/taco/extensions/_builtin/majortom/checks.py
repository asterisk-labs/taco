"""MajorTOM validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate MajorTOM metadata."""
    document = dataset.collection_json
    issues: list[tuple[str, str]] = []
    extra = document.get("majortom:extra")
    if isinstance(extra, dict):
        expected = {"majortom:code", *(f"majortom:{name}" for name in extra)}
        stored = {name for name in document["taco:metadata"].get("sample", {}) if name.startswith("majortom:")}
        if stored != expected:
            issues.append(("extensions", f"majortom columns {sorted(stored)} do not match majortom:extra"))
    for name in ("majortom:latitude_range", "majortom:longitude_range"):
        value = document.get(name)
        if isinstance(value, list) and len(value) == 2 and not value[0] < value[1]:
            issues.append(("extensions", f"{name} must increase, got {value}"))
    return issues
