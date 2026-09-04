from __future__ import annotations

import os
import struct
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa

from ._view import DatasetView, open_view
from .contract.collection import KNOWN_TASKS
from .contract.contract import COLLECTION_LEVEL, Contract
from .contract.naming import (
    COLLECTION_FILENAME,
    CURRENT_ID,
    DATA_DIR,
    METADATA_DIR,
    OFFSET,
    PARENT_ID,
    RELATIVE_PATH,
    SIZE,
    SOURCE_FILE,
    level_folder,
    level_to_filename,
)
from .contract.types import type_name
from .cozip import INDEX_NAME
from .errors import TacoError, ValidationFailed
from .writer.levels import level_schema

__all__ = ["Issue", "ValidationReport", "validate"]

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass
class ValidationReport:
    path: Path
    container: str | None
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if not self.ok:
            summary = "\n".join(str(issue) for issue in self.errors)
            raise ValidationFailed(f"{self.path} is not a valid TACO dataset:\n{summary}")

    def __str__(self) -> str:
        head = f"{self.path} ({self.container or 'unknown'}): "
        if not self.issues:
            return head + "valid"
        head += f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        return "\n".join([head, *(f"  {issue}" for issue in self.issues)])


class _Collector:
    def __init__(self, path: Path) -> None:
        self.report = ValidationReport(path, None)

    def error(self, code: str, message: str) -> None:
        self.report.issues.append(Issue("error", code, message))

    def warning(self, code: str, message: str) -> None:
        self.report.issues.append(Issue("warning", code, message))


def validate(path: str | os.PathLike[str], *, check_data: bool = True) -> ValidationReport:
    """Validate a dataset and return a report; it never raises for findings.

    ``check_data`` compares the metadata against the actual files or ZIP
    entries (slower on large datasets but catches broken offsets).
    """
    location = Path(path).expanduser()
    collector = _Collector(location)
    try:
        dataset = open_view(location)
    except TacoError as exc:
        collector.error("container", str(exc))
        return collector.report
    collector.report.container = dataset.container

    _check_collection(dataset, collector)
    _check_metadata_files(dataset, collector)
    if dataset.container == "tacocat":
        for source, tables in _split_by_source(dataset).items():
            _check_levels(dataset, tables, collector, label=f"{source}: ")
    else:
        _check_levels(dataset, dataset.tables, collector)
    if dataset.container == "zip":
        _check_zip(dataset, collector, check_data=check_data)
    elif dataset.container == "folder":
        _check_folder(dataset, collector, check_data=check_data)
    else:
        _check_tacocat(dataset, collector)
    return collector.report


def _check_collection(dataset: DatasetView, collector: _Collector) -> None:
    collection = dataset.collection
    unknown = [task for task in collection.tasks if task not in KNOWN_TASKS]
    if unknown:
        collector.warning("tasks", f"unrecognized task types {unknown}")
    if not collection.title:
        collector.warning("title", "COLLECTION.json has no title")


def _expected_schema_names(contract: Contract, level: str, container: str) -> list[str]:
    schema = level_schema(contract, level, with_offsets=container != "folder")
    names = list(schema.names)
    if container == "tacocat":
        names.append(SOURCE_FILE)
    return names


def _check_metadata_files(dataset: DatasetView, collector: _Collector) -> None:
    contract = dataset.contract
    for level in contract.levels:
        table = dataset.tables.get(level)
        if table is None:
            collector.error("metadata", f"missing METADATA file for level {level!r} ({level_to_filename(level)})")
        else:
            _check_schema(dataset, level, table, collector)


def _split_by_source(dataset: DatasetView) -> dict[str, dict[str, pa.Table]]:
    """Slice TACOCAT tables per partition; ids restart in every source file."""
    import pyarrow.compute as pc

    sources: set[str] = set()
    for table in dataset.tables.values():
        if SOURCE_FILE in table.column_names:
            sources.update(value for value in table.column(SOURCE_FILE).to_pylist() if value is not None)
    result: dict[str, dict[str, pa.Table]] = {}
    for source in sorted(sources):
        subset: dict[str, pa.Table] = {}
        for level, table in dataset.tables.items():
            if SOURCE_FILE in table.column_names:
                subset[level] = table.filter(pc.equal(table.column(SOURCE_FILE), source))  # type: ignore[attr-defined]
        result[source] = subset
    return result


def _check_levels(
    dataset: DatasetView,
    tables: dict[str, pa.Table],
    collector: _Collector,
    *,
    label: str = "",
) -> None:
    contract = dataset.contract
    sample_count = tables[COLLECTION_LEVEL].num_rows if COLLECTION_LEVEL in tables else 0
    parent_rows: dict[str, int] = {}
    parent_tables: dict[tuple[str, ...], str] = {(): COLLECTION_LEVEL}
    for level in contract.levels:
        table = tables.get(level)
        if table is None:
            continue
        rows = table.num_rows
        parent_rows[level] = rows
        if CURRENT_ID in table.column_names:
            ids = table.column(CURRENT_ID).to_pylist()
            if ids != list(range(rows)):
                collector.error("current_id", f"{label}{level}: internal:current_id must equal the row position")
        if level == COLLECTION_LEVEL:
            if RELATIVE_PATH in table.column_names:
                paths = table.column(RELATIVE_PATH).to_pylist()
                if paths != [str(index) for index in range(rows)]:
                    collector.error(
                        "relative_path", f"{label}collection: internal:relative_path must be the sample index"
                    )
            continue

        folder = level_folder(level)
        parent_level = parent_tables.get(folder)
        parent_tables[folder] = level
        if parent_level is None or PARENT_ID not in table.column_names or RELATIVE_PATH not in table.column_names:
            continue
        parent_count = parent_rows.get(parent_level, 0)
        parent_ids = table.column(PARENT_ID).to_pylist()
        paths = table.column(RELATIVE_PATH).to_pylist()
        bad_parent = [index for index, value in enumerate(parent_ids) if value is None or value >= parent_count]
        if bad_parent:
            collector.error(
                "parent_id",
                f"{label}{level}: {len(bad_parent)} rows reference a missing parent (first row {bad_parent[0]})",
            )
        prefix_ok = True
        children: dict[int, list[str]] = defaultdict(list)
        for parent_id, relative_path in zip(parent_ids, paths, strict=True):
            parts = relative_path.split("/")
            expected_depth = 2 + len(folder)
            if len(parts) != expected_depth or not parts[0].isdigit() or int(parts[0]) >= sample_count:
                prefix_ok = False
                continue
            if tuple(parts[1:-1]) != folder:
                prefix_ok = False
                continue
            children[parent_id].append(parts[-1])
        if not prefix_ok:
            collector.error(
                "relative_path",
                f"{label}{level}: internal:relative_path entries do not follow '<sample>/{'/'.join(folder) or ''}<name>'",
            )
        _check_children(contract, level, folder, children, parent_count, collector, label=label)


def _check_children(
    contract: Contract,
    level: str,
    folder: tuple[str, ...],
    children: dict[int, list[str]],
    parent_count: int,
    collector: _Collector,
    *,
    label: str = "",
) -> None:
    entries = contract.children(folder)
    fixed = {item for kind, item in entries if kind == "folder"} | {
        item.name for kind, item in entries if kind == "leaf" and not item.variable
    }
    variables = [item for kind, item in entries if kind == "leaf" and item.variable]
    problems = 0
    for parent in range(parent_count):
        names = children.get(parent, [])
        seen = set(names)
        if len(seen) != len(names):
            problems += 1
            continue
        if fixed - seen:
            problems += 1
            continue
        remaining = seen - fixed
        for leaf in variables:
            indexes = sorted(index for name in list(remaining) if (index := leaf.match_index(name)) is not None)
            for name in list(remaining):
                if leaf.match_index(name) is not None:
                    remaining.discard(name)
            if indexes != list(range(len(indexes))) or not (leaf.minimum <= len(indexes) <= leaf.maximum):
                problems += 1
                break
        if remaining:
            problems += 1
    if problems:
        collector.error(
            "structure", f"{label}{level}: {problems} parent(s) have children that do not match the contract"
        )


def _check_schema(dataset: DatasetView, level: str, table: pa.Table, collector: _Collector) -> None:
    contract = dataset.contract
    expected = _expected_schema_names(contract, level, dataset.container)
    actual = table.column_names
    missing = [name for name in expected if name not in actual]
    extra = [name for name in actual if name not in expected]
    if missing:
        collector.error("schema", f"{level}: missing columns {missing}")
    if extra:
        collector.error("schema", f"{level}: unexpected columns {extra}")
    reference = level_schema(contract, level, with_offsets=dataset.container != "folder")
    for field_ in reference:
        if field_.name in actual:
            actual_type = table.schema.field(field_.name).type
            if type_name(actual_type) != type_name(field_.type):
                collector.error(
                    "schema",
                    f"{level}: column {field_.name!r} is {type_name(actual_type)}, contract says {type_name(field_.type)}",
                )
    for name in (OFFSET, SIZE):
        if name in actual and dataset.container != "folder":
            values = table.column(name).to_pylist()
            paths = table.column(RELATIVE_PATH).to_pylist() if RELATIVE_PATH in actual else []
            for relative_path, value in zip(paths, values, strict=False):
                is_leaf = dataset.is_leaf_level_row(level, relative_path)
                if is_leaf and value is None:
                    collector.error("offsets", f"{level}: file row {relative_path!r} has no {name}")
                    break
                if not is_leaf and value is not None:
                    collector.error("offsets", f"{level}: folder row {relative_path!r} must not carry {name}")
                    break


def _local_data_offsets(zf: zipfile.ZipFile, stream: Any) -> Iterator[tuple[str, int, int, zipfile.ZipInfo]]:
    for info in zf.infolist():
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) < 30 or header[:4] != b"PK\x03\x04":
            raise ValueError(f"bad local header for {info.filename}")
        name_length, extra_length = struct.unpack_from("<HH", header, 26)
        yield info.filename, info.header_offset + 30 + name_length + extra_length, info.file_size, info


def _check_zip(dataset: DatasetView, collector: _Collector, *, check_data: bool) -> None:
    # Opening the dataset already ran the reader, which enforces the cozip
    # layer: the byte-0 header, ASCII and reserved names, the integrity hash
    # and every declared byte range. What is left is TACO's own contract with
    # the archive around it.
    expected = {COLLECTION_FILENAME, *(f"{METADATA_DIR}/{level_to_filename(level)}" for level in dataset.levels)}
    if not check_data:
        return

    expected_data = {row.archive_name: (row.offset, row.size) for row in dataset.iter_data_rows()}
    seen_data: dict[str, tuple[int, int]] = {}
    try:
        with zipfile.ZipFile(dataset.path) as archive, dataset.path.open("rb") as stream:
            if archive.comment:
                collector.error("zip", "archive comment must be empty")
            names = [info.filename for info in archive.infolist()]
            if not names or names[0] != INDEX_NAME:
                collector.error("zip", "__cozip__ must be the first entry")
            block = names[-len(expected) :] if len(names) >= len(expected) else []
            if set(block) != expected:
                collector.error("zip", "priority files must form the final contiguous entry block")
            for name, data_offset, size, info in _local_data_offsets(archive, stream):
                if info.compress_type != zipfile.ZIP_STORED:
                    collector.error("zip", f"entry {name!r} is not STORE")
                if info.is_dir():
                    collector.error("zip", f"explicit directory entry {name!r} is forbidden")
                if name.startswith(DATA_DIR + "/"):
                    seen_data[name] = (data_offset, size)
    except (zipfile.BadZipFile, ValueError) as exc:
        collector.error("zip", f"cannot read ZIP structure: {exc}")
        return

    missing_entries = sorted(set(expected_data) - set(seen_data))
    extra_entries = sorted(set(seen_data) - set(expected_data))
    if missing_entries:
        collector.error(
            "data",
            f"{len(missing_entries)} data files in metadata are missing from the archive (first: {missing_entries[0]})",
        )
    if extra_entries:
        collector.error(
            "data",
            f"{len(extra_entries)} DATA entries are not described by metadata (first: {extra_entries[0]})",
        )
    mismatched = [name for name, location in expected_data.items() if name in seen_data and seen_data[name] != location]
    if mismatched:
        collector.error(
            "offsets",
            f"{len(mismatched)} data rows have offsets that do not match the archive (first: {mismatched[0]})",
        )


def _check_folder(dataset: DatasetView, collector: _Collector, *, check_data: bool) -> None:
    if not check_data:
        return
    data_root = dataset.path / DATA_DIR
    expected = {row.relative_path for row in dataset.iter_data_rows()}
    missing = 0
    empty = 0
    first_missing = None
    for relative_path in expected:
        file = data_root / relative_path
        if not file.is_file():
            missing += 1
            first_missing = first_missing or relative_path
        elif file.stat().st_size == 0:
            empty += 1
    if missing:
        collector.error("data", f"{missing} data files referenced by metadata are missing (first: {first_missing})")
    if empty:
        collector.warning("data", f"{empty} data files are empty; they cannot be packed into an archive")
    if data_root.is_dir():
        present = {file.relative_to(data_root).as_posix() for file in data_root.rglob("*") if file.is_file()}
        extra = sorted(present - expected)
        if extra:
            collector.warning(
                "data", f"{len(extra)} files under DATA/ are not described by metadata (first: {extra[0]})"
            )


def _check_tacocat(dataset: DatasetView, collector: _Collector) -> None:
    sources = dataset.collection_json.get("taco:sources")
    if not isinstance(sources, dict) or "files" not in sources:
        collector.warning("sources", "COLLECTION.json has no taco:sources")
        listed: list[str] = []
    else:
        listed = list(sources.get("files", []))
        for name in listed:
            if not (dataset.path.parent / name).is_file():
                collector.warning("sources", f"partition {name!r} is not next to the .tacocat directory")
    for level, table in dataset.tables.items():
        if SOURCE_FILE not in table.column_names:
            collector.error("source_file", f"{level}: TACOCAT tables need internal:source_file")
            continue
        unknown = sorted(set(table.column(SOURCE_FILE).to_pylist()) - set(listed)) if listed else []
        if unknown:
            collector.error(
                "source_file", f"{level}: rows reference partitions not listed in taco:sources: {unknown[:3]}"
            )
