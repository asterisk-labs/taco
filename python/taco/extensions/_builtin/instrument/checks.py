"""Instrument validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate stored instrument metadata."""
    document = dataset.collection_json
    stored_files = document.get("instrument:files")
    records = document.get("instrument:instruments")
    if not isinstance(stored_files, dict) or not isinstance(records, dict):
        return []
    issues: list[tuple[str, str]] = []
    unknown = sorted(set(stored_files) - set(dataset.contract.structure))
    if unknown:
        issues.append(("extensions", f"instrument files are not in taco:structure {unknown}"))
    for path, file in stored_files.items():
        if not isinstance(file, dict):
            continue
        identifiers = file.get("instruments")
        bands = file.get("bands")
        if not isinstance(identifiers, list) or not isinstance(bands, list):
            continue
        if not all(isinstance(value, str) for value in (*identifiers, *bands)):
            continue
        for identifier in identifiers:
            record = records.get(identifier)
            if not isinstance(record, dict):
                issues.append(("extensions", f"{identifier!r} used by {path!r} has no instrument record"))
                continue
            stored_bands = record.get("bands")
            if not isinstance(stored_bands, dict):
                continue
            missing = [band for band in bands if band not in stored_bands]
            if missing:
                issues.append(("extensions", f"{path!r} lists bands {missing} that {identifier!r} does not have"))
    return issues
