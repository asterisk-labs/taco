"""Split validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import PARTITIONS, Counts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate stored splits and their collection counts."""
    table = dataset.tables.get("sample")
    if table is None or "split:split" not in table.column_names:
        return []
    issues: list[tuple[str, str]] = []
    values = table.column("split:split").to_pylist()
    rows = [row for row, value in enumerate(values) if value not in PARTITIONS]
    if rows:
        issues.append(
            (
                "extensions",
                f"sample: split:split has {len(rows)} values outside {list(PARTITIONS)} "
                f"(first row {rows[0]}: {values[rows[0]]!r})",
            )
        )
        return issues
    summary = Counts()
    summary.update({"split": values})
    stored = dataset.collection_json.get(Counts.field)
    if stored is not None and stored != summary.finish():
        issues.append(("extensions", f"split:counts {stored} does not match the stored rows {summary.finish()}"))
    return issues
