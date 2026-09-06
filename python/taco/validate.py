from __future__ import annotations

import struct
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import BinaryIO, Literal

import pyarrow as pa

from ._view import DatasetView, open_view
from .contract.collection import KNOWN_TASKS
from .contract.contract import CHILDREN_LEVEL, SAMPLE_LEVEL, Contract
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


def validate(path: str | PathLike[str], *, check_data: bool = True) -> ValidationReport:
    """Validate a dataset and return a report; it never raises for findings.

    ``check_data`` compares the metadata against the actual files or ZIP
    entries (slower on large datasets but catches broken offsets).
    """
    location = Path(path).expanduser()
    collector = _Collector(location)
    try:
        dataset = open_view(location)
    except (TacoError, OSError, ValueError) as exc:
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
    if dataset.container != "tacocat" and collection.sources is not None:
        collector.error("sources", "taco:sources is only valid in TACOCAT")
    unknown = [task for task in collection.tasks if task not in KNOWN_TASKS]
    if unknown:
        collector.warning("tasks", f"unrecognized task types {unknown}")


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
    if dataset.container == "folder":
        directory = dataset.path / METADATA_DIR
    elif dataset.container == "tacocat":
        directory = dataset.path
    else:
        return
    expected = {level_to_filename(level) for level in contract.levels}
    extra = sorted(path.name for path in directory.glob("*.parquet") if path.name not in expected)
    if extra:
        collector.error("metadata", f"unexpected Parquet files {extra}")


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
                mask = pc.equal(table.column(SOURCE_FILE), source)  # type: ignore[attr-defined]
                subset[level] = table.filter(mask)
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
    sample_count = tables[SAMPLE_LEVEL].num_rows if SAMPLE_LEVEL in tables else 0
    parent_rows: dict[str, int] = {}
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
        if level == SAMPLE_LEVEL:
            if RELATIVE_PATH in table.column_names:
                paths = table.column(RELATIVE_PATH).to_pylist()
                if paths != [str(index) for index in range(rows)]:
                    collector.error("relative_path", f"{label}sample: internal:relative_path must be the sample index")
            continue

        folder = level_folder(level)
        parent_level = SAMPLE_LEVEL if level == CHILDREN_LEVEL else contract.level_of_folder(folder[:-1])
        if PARENT_ID not in table.column_names or RELATIVE_PATH not in table.column_names:
            continue
        parent_table = tables.get(parent_level)
        if parent_table is None:
            continue
        parent_count = parent_rows.get(parent_level, 0)
        if folder:
            current_ids = parent_table.column(CURRENT_ID).to_pylist()
            parent_paths = parent_table.column(RELATIVE_PATH).to_pylist()
            valid_parents = {
                current_id
                for current_id, relative_path in zip(current_ids, parent_paths, strict=True)
                if isinstance(current_id, int)
                and isinstance(relative_path, str)
                and tuple(relative_path.split("/")[1:]) == folder
            }
        else:
            valid_parents = set(range(parent_count))
        parent_ids = table.column(PARENT_ID).to_pylist()
        paths = table.column(RELATIVE_PATH).to_pylist()
        bad_parent = [
            index
            for index, value in enumerate(parent_ids)
            if not isinstance(value, int) or isinstance(value, bool) or value not in valid_parents
        ]
        if bad_parent:
            collector.error(
                "parent_id",
                f"{label}{level}: {len(bad_parent)} rows reference a missing parent (first row {bad_parent[0]})",
            )
        prefix_ok = True
        children: dict[int, list[str]] = defaultdict(list)
        for parent_id, relative_path in zip(parent_ids, paths, strict=True):
            if not isinstance(relative_path, str):
                prefix_ok = False
                continue
            parts = relative_path.split("/")
            expected_depth = 2 + len(folder)
            if (
                len(parts) != expected_depth
                or not parts[0].isascii()
                or not parts[0].isdigit()
                or (len(parts[0]) > 1 and parts[0].startswith("0"))
                or int(parts[0]) >= sample_count
            ):
                prefix_ok = False
                continue
            if tuple(parts[1:-1]) != folder:
                prefix_ok = False
                continue
            if isinstance(parent_id, int) and not isinstance(parent_id, bool):
                children[parent_id].append(parts[-1])
        if not prefix_ok:
            collector.error(
                "relative_path",
                f"{label}{level}: internal:relative_path entries do not follow '<sample>/{'/'.join(folder) or ''}<name>'",
            )
        _check_children(contract, level, folder, children, valid_parents, collector, label=label)


def _check_children(
    contract: Contract,
    level: str,
    folder: tuple[str, ...],
    children: dict[int, list[str]],
    parent_ids: set[int],
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
    for parent in parent_ids:
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
    schema_metadata = table.schema.metadata or {}
    if schema_metadata.get(b"taco:level") != level.encode():
        collector.error("schema", f"{level}: Parquet schema must declare taco:level={level!r}")
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
            column = table.column(field_.name)
            actual_field = table.schema.field(field_.name)
            actual_type = actual_field.type
            if type_name(actual_type) != type_name(field_.type):
                collector.error(
                    "schema",
                    f"{level}: column {field_.name!r} is {type_name(actual_type)}, contract says {type_name(field_.type)}",
                )
            if actual_field.nullable != field_.nullable:
                collector.error("schema", f"{level}: column {field_.name!r} nullability does not match the contract")
            expected_description = (field_.metadata or {}).get(b"description")
            actual_description = (actual_field.metadata or {}).get(b"description")
            if actual_description != expected_description:
                collector.error("schema", f"{level}: column {field_.name!r} description does not match the contract")
            if not field_.nullable and column.null_count:
                collector.error("schema", f"{level}: column {field_.name!r} contains null values")
    if dataset.container == "tacocat" and SOURCE_FILE in actual:
        source_field = table.schema.field(SOURCE_FILE)
        source_column = table.column(SOURCE_FILE)
        if not pa.types.is_string(source_column.type):
            collector.error("schema", f"{level}: column {SOURCE_FILE!r} must be string")
        if source_field.nullable:
            collector.error("schema", f"{level}: column {SOURCE_FILE!r} must not be nullable")
        if source_column.null_count:
            collector.error("schema", f"{level}: column {SOURCE_FILE!r} contains null values")
    for name in (OFFSET, SIZE):
        if name in actual and dataset.container != "folder":
            values = table.column(name).to_pylist()
            paths = table.column(RELATIVE_PATH).to_pylist() if RELATIVE_PATH in actual else []
            for relative_path, value in zip(paths, values, strict=False):
                is_leaf = dataset.is_leaf_level_row(level, relative_path)
                if is_leaf and value is None:
                    collector.error("offsets", f"{level}: file row {relative_path!r} has no {name}")
                    break
                if is_leaf and name == SIZE and value is not None and value == 0:
                    collector.error("offsets", f"{level}: file row {relative_path!r} has zero size")
                    break
                if not is_leaf and value is not None:
                    collector.error("offsets", f"{level}: folder row {relative_path!r} must not carry {name}")
                    break


def _local_data_offsets(zf: zipfile.ZipFile, stream: BinaryIO) -> Iterator[tuple[str, int, int, zipfile.ZipInfo]]:
    for info in zf.infolist():
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) < 30 or header[:4] != b"PK\x03\x04":
            raise ValueError(f"bad local header for {info.filename}")
        name_length, extra_length = struct.unpack_from("<HH", header, 26)
        yield info.filename, info.header_offset + 30 + name_length + extra_length, info.file_size, info


def _check_zip(dataset: DatasetView, collector: _Collector, *, check_data: bool) -> None:
    from .reader import levels

    try:
        indexed_levels = levels(dataset.path)
        if set(indexed_levels) != set(dataset.levels):
            collector.error("cozip", "the CoZIP index does not match taco:metadata")
    except Exception as exc:
        collector.error("cozip", str(exc))

    expected = {COLLECTION_FILENAME, *(f"{METADATA_DIR}/{level_to_filename(level)}" for level in dataset.levels)}
    expected_data = {row.archive_name: (row.offset, row.size) for row in dataset.iter_data_rows()} if check_data else {}
    seen_data: dict[str, tuple[int, int]] = {}
    try:
        with zipfile.ZipFile(dataset.path) as archive, dataset.path.open("rb") as stream:
            if archive.comment:
                collector.error("zip", "archive comment must be empty")
            names = [info.filename for info in archive.infolist()]
            duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
            if duplicates:
                collector.error("zip", f"duplicate ZIP entries are forbidden (first: {duplicates[0]})")
            if not names or names[0] != INDEX_NAME:
                collector.error("zip", "__cozip__ must be the first entry")
            block = names[-len(expected) :] if len(names) >= len(expected) else []
            if set(block) != expected:
                collector.error("zip", "priority files must form the final contiguous entry block")
            metadata_entries = {
                name.removeprefix(METADATA_DIR + "/")
                for name in names
                if name.startswith(METADATA_DIR + "/") and name.endswith(".parquet")
            }
            expected_metadata = {level_to_filename(level) for level in dataset.levels}
            extra_metadata = sorted(metadata_entries - expected_metadata)
            if extra_metadata:
                collector.error("metadata", f"unexpected Parquet files {extra_metadata}")
            for name, data_offset, size, info in _local_data_offsets(archive, stream):
                if info.compress_type != zipfile.ZIP_STORED:
                    collector.error("zip", f"entry {name!r} is not STORE")
                if info.is_dir():
                    collector.error("zip", f"explicit directory entry {name!r} is forbidden")
                if check_data and name.startswith(DATA_DIR + "/"):
                    seen_data[name] = (data_offset, size)
    except (zipfile.BadZipFile, ValueError) as exc:
        collector.error("zip", f"cannot read ZIP structure: {exc}")
        return

    if not check_data:
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
    data_root = dataset.path / DATA_DIR
    if not data_root.is_dir():
        collector.error("data", f"missing {DATA_DIR}/ directory")
    if not check_data:
        return
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
        collector.error("data", f"{empty} data files are empty")
    if data_root.is_dir():
        present = {file.relative_to(data_root).as_posix() for file in data_root.rglob("*") if file.is_file()}
        extra = sorted(present - expected)
        if extra:
            collector.warning(
                "data", f"{len(extra)} files under DATA/ are not described by metadata (first: {extra[0]})"
            )


def _check_tacocat(dataset: DatasetView, collector: _Collector) -> None:
    sources = dataset.collection_json.get("taco:sources")
    if not isinstance(sources, dict) or "partitions" not in sources:
        collector.error("sources", "COLLECTION.json has no taco:sources.partitions")
        listed: list[str] = []
    else:
        partitions = sources.get("partitions")
        if not isinstance(partitions, list) or not all(isinstance(item, dict) for item in partitions):
            collector.error("sources", "taco:sources.partitions must be a list of objects")
            listed = []
        else:
            listed = [item.get("file") for item in partitions if isinstance(item.get("file"), str)]
            if len(listed) != len(partitions):
                collector.error("sources", "every partition needs a file")
            if len(set(listed)) != len(listed):
                collector.error("sources", "taco:sources contains duplicate files")
            counts = [item.get("samples") for item in partitions]
            if not all(isinstance(value, int) and value >= 0 for value in counts):
                collector.error("sources", "every partition needs a non-negative sample count")
            elif sources.get("samples") != sum(counts):
                collector.error("sources", "taco:sources.samples does not match its partitions")
            sample_table = dataset.tables.get(SAMPLE_LEVEL)
            if sample_table is not None and SOURCE_FILE in sample_table.column_names:
                actual = Counter(sample_table.column(SOURCE_FILE).to_pylist())
                expected = {item["file"]: item["samples"] for item in partitions if "file" in item}
                if actual != expected:
                    collector.error("sources", "partition sample counts do not match sample.parquet")
        for name in listed:
            if Path(name).is_absolute():
                collector.error("sources", f"partition path must be relative: {name!r}")
            elif not (dataset.path.parent / name).is_file():
                collector.warning("sources", f"partition {name!r} cannot be found")
    for level, table in dataset.tables.items():
        if SOURCE_FILE not in table.column_names:
            collector.error("source_file", f"{level}: TACOCAT tables need internal:source_file")
            continue
        unknown = sorted(set(table.column(SOURCE_FILE).to_pylist()) - set(listed)) if listed else []
        if unknown:
            collector.error(
                "source_file", f"{level}: rows reference partitions not listed in taco:sources: {unknown[:3]}"
            )
