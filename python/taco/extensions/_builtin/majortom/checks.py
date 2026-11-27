"""MajorTOM validation."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from .extension import MajorTOM

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView

# Rows whose codes are computed again at once.
_BATCH_ROWS = 65_536


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate MajorTOM metadata."""
    document = dataset.collection_json
    issues: list[tuple[str, str]] = []
    levels = sorted(
        level
        for level, fields in document["taco:metadata"].items()
        if level != "sample" and any(name.startswith("majortom:") for name in fields)
    )
    if levels:
        issues.append(("extensions", f"majortom columns must be at the sample level, found at {levels}"))
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
    issues.extend(_code_issues(dataset))
    return issues


def _stored_extension(document: Mapping[str, Any]) -> MajorTOM | None:
    """The extension the stored parameters describe; the schema reports parameters it cannot take."""
    try:
        return MajorTOM(
            dist_km=document["majortom:dist_km"],
            extra=document["majortom:extra"],
            latitude_range=tuple(document["majortom:latitude_range"]),
            longitude_range=tuple(document["majortom:longitude_range"]),
            sep=document["majortom:sep"],
            centroid=document["majortom:centroid"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _stored_row(offset: int, row: int) -> str:
    return f"row {offset + row}"


def _code_issues(dataset: DatasetView) -> list[tuple[str, str]]:
    """Compute the codes again from the stored centroids and parameters."""
    extension = _stored_extension(dataset.collection_json)
    table = dataset.tables.get("sample")
    if extension is None or table is None:
        return []
    if extension.centroid not in dataset.contract.metadata["sample"]:
        return [("extensions", f"majortom:centroid {extension.centroid!r} is not a field of the sample level")]
    # The schema checks report missing columns.
    names = [name for name, _ in extension._grids() if f"majortom:{name}" in table.column_names]
    if extension.centroid not in table.column_names or not names:
        return []
    columns = table.select([extension.centroid, *(f"majortom:{name}" for name in names)])
    wrong: dict[str, list[Any]] = {}
    offset = 0
    for batch in columns.to_batches(max_chunksize=_BATCH_ROWS):
        try:
            expected = extension._grid_codes(batch.column(0).to_pylist(), partial(_stored_row, offset))
        except ValueError as exc:
            return [("extensions", f"sample: {exc}")]
        for position, name in enumerate(names, start=1):
            stored = batch.column(position).to_pylist()
            for row, (value, code) in enumerate(zip(stored, expected[name], strict=True)):
                if value != code:
                    found = wrong.setdefault(name, [0, offset + row, value, code])
                    found[0] += 1
        offset += batch.num_rows
    return [
        (
            "extensions",
            f"sample: majortom:{name} does not match the centroids and parameters on {count} rows "
            f"(first row {row}: {value!r}, expected {code!r})",
        )
        for name, (count, row, value, code) in wrong.items()
    ]
