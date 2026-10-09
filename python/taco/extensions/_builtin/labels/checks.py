"""Labels validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate labels files against the structure and their names."""
    files = dataset.collection_json.get("labels:files")
    if not isinstance(files, dict):
        return []
    issues: list[tuple[str, str]] = []
    unknown = sorted(set(files) - set(dataset.contract.structure))
    if unknown:
        issues.append(("extensions", f"labels files {unknown} are not in taco:structure"))
    for path, labels in files.items():
        if (
            isinstance(labels, dict)
            and all(isinstance(name, str) for name in labels.values())
            and len(set(labels.values())) != len(labels)
        ):
            issues.append(("extensions", f"labels of {path!r} must not repeat a name"))
    return issues
