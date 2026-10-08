"""STAC profile validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ....contract.schema import PROFILE_FIELDS
from .models import STAC, Spatial, Temporal, grid_problems

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....container.view import DatasetView


def _profile_problem(values: dict[str, object]) -> str | None:
    try:
        if "centroid" in values:
            if values["centroid"] is None:
                return "centroid is null"
            model = STAC if "datetime" in values else Spatial
            model.model_validate(values)
        else:
            Temporal.model_validate(values)
    except ValueError as exc:
        return str(exc)
    return None


def _grid_problems(grids: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, str]]:
    problems = grid_problems(
        [values["proj_code"] for _, values in grids],
        [values["proj_shape"] for _, values in grids],
        [values["proj_transform"] for _, values in grids],
    )
    return [(grids[index][0], message) for index, message in problems]


def check_dataset(dataset: DatasetView) -> list[tuple[str, str]]:
    """Validate stored profile rows."""
    issues: list[tuple[str, str]] = []
    for level, fields in dataset.contract.metadata.items():
        table = dataset.tables.get(level)
        if table is None:
            continue
        namespaces = {name.partition(":")[0] for name in fields}.intersection(PROFILE_FIELDS)
        for namespace in sorted(namespaces):
            names = list(PROFILE_FIELDS[namespace])
            if not all(f"{namespace}:{name}" in table.column_names for name in names):
                continue
            selected = table.select([f"{namespace}:{name}" for name in names])
            first_problem: tuple[int, str] | None = None
            problem_count = 0
            offset = 0
            for batch in selected.to_batches(max_chunksize=8192):
                columns = [column.to_pylist() for column in batch.columns]
                problems: list[tuple[int, str]] = []
                grids: list[tuple[int, dict[str, Any]]] = []
                for row, values in enumerate(zip(*columns, strict=True), start=offset):
                    if all(value is None for value in values):
                        continue
                    named = dict(zip(names, values, strict=True))
                    problem = _profile_problem(named)
                    if problem is not None:
                        problems.append((row, problem))
                    elif named.get("proj_code") is not None:
                        grids.append((row, named))
                problems.extend(_grid_problems(grids))
                problem_count += len(problems)
                if problems and first_problem is None:
                    first_problem = min(problems)
                offset += batch.num_rows
            if first_problem is not None:
                row, problem = first_problem
                issues.append(
                    (
                        "profile",
                        f"{level}: {namespace} breaks its profile on {problem_count} rows (first row {row}: {problem})",
                    )
                )
    return issues
