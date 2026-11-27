from __future__ import annotations

import math
import struct
import zipfile
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any, BinaryIO, Literal

import pyarrow as pa

from .container.cozip import INDEX_NAME
from .container.view import DatasetView, open_view
from .contract.collection import KNOWN_TASKS, Extent
from .contract.contract import CHILDREN_LEVEL, SAMPLE_ID, SAMPLE_LEVEL, Contract
from .contract.extension import CollectionSummary
from .contract.extension_registry import OWNERS, checks, schema, unbundled, used_builtins, used_namespaces
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
    normalize_relative_path,
)
from .contract.types import type_name
from .errors import TacoError, ValidationFailed
from .writer.metadata import collection_summaries, summary_types, table_schema

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
    def __init__(self, path: Path, label: str = "", report: ValidationReport | None = None) -> None:
        self.report = ValidationReport(path, None) if report is None else report
        self.label = label

    def labeled(self, label: str) -> _Collector:
        """Add to the same report, with every message prefixed by ``label``."""
        return _Collector(self.report.path, self.label + label, self.report)

    def error(self, code: str, message: str) -> None:
        self.report.issues.append(Issue("error", code, self.label + message))

    def warning(self, code: str, message: str) -> None:
        self.report.issues.append(Issue("warning", code, self.label + message))


def validate(path: str | PathLike[str], *, check_data: bool = True) -> ValidationReport:
    """Return all validation errors and warnings for a local dataset.

    ``check_data`` also checks files or ZIP entries against their metadata and
    the CRC-32 of every ZIP payload, in TACOCAT partitions too.
    """
    location = Path(path).expanduser()
    collector = _Collector(location)
    try:
        dataset = open_view(location)
    except (TacoError, OSError, ValueError) as exc:
        collector.error("container", str(exc))
        return collector.report
    collector.report.container = dataset.container
    _check_dataset(dataset, collector, check_data=check_data)
    return collector.report


def _check_dataset(dataset: DatasetView, collector: _Collector, *, check_data: bool) -> None:
    _check_collection(dataset, collector)
    _check_extensions(dataset, collector)
    _check_metadata_files(dataset, collector)
    _check_sample_ids(dataset, collector)
    _check_levels(dataset, dataset.tables, collector)
    _check_extension_rules(dataset, collector)
    # TACOCAT summaries come from its partitions, not its merged rows.
    if dataset.container != "tacocat":
        _check_extent(dataset, collector)
    if dataset.container == "zip":
        _check_zip(dataset, collector, check_data=check_data)
    elif dataset.container == "folder":
        _check_folder(dataset, collector, check_data=check_data)
    else:
        _check_tacocat(dataset, collector, check_data=check_data)


def _check_sample_ids(dataset: DatasetView, collector: _Collector) -> None:
    """Check the whole dataset at once.

    In a TACOCAT the per-source tables are validated separately, but `id` is
    unique across partitions, so this reads the merged sample table.
    """
    table = dataset.tables.get(SAMPLE_LEVEL)
    if table is None:
        return
    if SAMPLE_ID not in table.column_names:
        collector.error("id", "sample: the id column is missing")
        return
    column = table.column(SAMPLE_ID)
    values = column.to_pylist()
    nulls = sum(value is None for value in values)
    if nulls:
        collector.error("id", f"sample: id is null on {nulls} rows")
    empty = sum(isinstance(value, str) and not value.strip() for value in values)
    if empty:
        collector.error("id", f"sample: id is empty on {empty} rows")
    counts = Counter(value for value in values if isinstance(value, str) and value.strip())
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    if duplicates:
        collector.error("id", f"sample: id must be unique ({duplicates} duplicates)")


def _check_collection(dataset: DatasetView, collector: _Collector) -> None:
    collection = dataset.collection
    # The native reader treats even a null taco:sources key as TACOCAT.
    if dataset.container != "tacocat" and "taco:sources" in dataset.collection_json:
        collector.error("sources", "taco:sources is only valid in TACOCAT")
    unknown = [task for task in collection.tasks or () if task not in KNOWN_TASKS]
    if unknown:
        collector.warning("tasks", f"unrecognized task types {unknown}")


# JSON Schema violations reported for each extension.
_SCHEMA_ERRORS = 10


def _check_extensions(dataset: DatasetView, collector: _Collector) -> None:
    document = dataset.collection_json
    if document.get("taco:extensions") is None:
        collector.error("extensions", "COLLECTION.json must list the extensions it uses in taco:extensions")
    listed = dataset.collection.extensions
    missing = unbundled(listed)
    if missing:
        collector.error("extensions", f"taco:extensions lists TACO extensions that are not bundled: {missing}")
    for namespace in sorted(used_namespaces(document)):
        owner = OWNERS.get(namespace)
        if owner is not None and owner not in listed:
            collector.error("extensions", f"{namespace} fields need {owner} in taco:extensions")
    unknown = [identifier for identifier in listed if schema(identifier) is None and identifier not in missing]
    if unknown:
        collector.warning("extensions", f"cannot check extensions this version does not know: {unknown}")

    import jsonschema

    for identifier in listed:
        definition = schema(identifier)
        if definition is None:
            continue
        errors = list(jsonschema.Draft202012Validator(definition).iter_errors(document))
        for violation in errors[:_SCHEMA_ERRORS]:
            # Inside anyOf or oneOf, the most relevant branch explains the violation.
            error = jsonschema.exceptions.best_match([violation])
            where = "/".join(str(part) for part in error.absolute_path) or "COLLECTION.json"
            message = error.message if len(error.message) <= 200 else error.message[:197] + "..."
            collector.error("extensions", f"{where} breaks {identifier}: {message}")
        if len(errors) > _SCHEMA_ERRORS:
            collector.error(
                "extensions", f"COLLECTION.json breaks {identifier} in {len(errors) - _SCHEMA_ERRORS} more places"
            )


def _check_extension_rules(dataset: DatasetView, collector: _Collector) -> None:
    """Run checks not covered by JSON Schema."""
    for builtin in used_builtins(dataset.collection_json):
        check = checks(builtin)
        if check is not None:
            for code, message in check(dataset):
                collector.error(code, message)


# Allow for platform-dependent PROJ rounding in grid footprints.
_EXTENT_TOLERANCE = 1e-9
# Match the writer's default reducer batch size.
_SUMMARY_ROWS = 10_000


def _row_extent(dataset: DatasetView) -> Extent | None:
    """Recompute the extent with the writer's reducers."""
    summaries = collection_summaries(dataset.contract)
    try:
        for summary in summaries:
            if summary.field == "extent":
                table = dataset.level(summary.level)
                for start in range(0, table.num_rows, _SUMMARY_ROWS):
                    summary.update_table(table.slice(start, _SUMMARY_ROWS))
                value = summary.reducer.finish()
                return None if value is None else Extent.from_any(value)
        return None
    finally:
        for summary in summaries:
            summary.reducer.close()


def _check_extent(dataset: DatasetView, collector: _Collector) -> None:
    """Compare the stored extent with the row summary (SPEC 5.5)."""
    try:
        expected = _row_extent(dataset)
    except Exception:
        # Schema and profile checks report rows the reducer cannot read.
        return
    stored = dataset.collection.extent
    if stored is None and expected is not None:
        collector.error("extensions", f"extent is missing; the stored rows give {expected.to_dict()}")
    elif stored is not None and expected is None:
        collector.error("extensions", "extent must be omitted, since no row has a spatial or stac location")
    elif stored is not None and expected is not None and not _same_extent(stored, expected):
        collector.error("extensions", f"extent {stored.to_dict()} does not match the stored rows {expected.to_dict()}")


def _same_extent(stored: Extent, expected: Extent) -> bool:
    if stored.temporal != expected.temporal:
        return False
    differences = [abs(left - right) for left, right in zip(stored.spatial, expected.spatial, strict=True)]
    # -180 and 180 name the same meridian.
    for index in (0, 2):
        differences[index] = min(differences[index], 360 - differences[index])
    return max(differences) <= _EXTENT_TOLERANCE


def _expected_schema_names(contract: Contract, level: str, container: str) -> list[str]:
    schema = table_schema(contract, level, with_offsets=container != "folder")
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
    # TACO 7.6 rule 2 allows nothing else beside the level files: in METADATA/,
    # or in a TACOCAT directory, which holds them with COLLECTION.json (7.5).
    if dataset.container == "folder":
        directory = dataset.path / METADATA_DIR
        allowed: set[str] = set()
    elif dataset.container == "tacocat":
        directory = dataset.path
        allowed = {COLLECTION_FILENAME}
    else:
        return
    allowed |= {level_to_filename(level) for level in contract.levels}
    extra = sorted(path.name for path in directory.iterdir() if path.name not in allowed)
    if extra:
        collector.error("metadata", f"unexpected files {extra}")


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
                if dataset.container == "tacocat" and SOURCE_FILE in table.column_names:
                    next_path: dict[str, int] = defaultdict(int)
                    valid = True
                    for source, path in zip(table.column(SOURCE_FILE).to_pylist(), paths, strict=True):
                        if path != str(next_path[source]):
                            valid = False
                        next_path[source] += 1
                else:
                    valid = paths == [str(index) for index in range(rows)]
                if not valid:
                    collector.error("relative_path", f"{label}sample: internal:relative_path must be the sample index")
            continue

        folder = level_folder(level)
        parent_level = SAMPLE_LEVEL if level == CHILDREN_LEVEL else contract.level_of_folder(folder[:-1])
        if PARENT_ID not in table.column_names or RELATIVE_PATH not in table.column_names:
            continue
        parent_table = tables.get(parent_level)
        # _check_schema reports missing parent columns.
        if parent_table is None or not {CURRENT_ID, RELATIVE_PATH}.issubset(parent_table.column_names):
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
        parent_sources = (
            parent_table.column(SOURCE_FILE).to_pylist()
            if SOURCE_FILE in parent_table.column_names
            else [None] * parent_table.num_rows
        )
        child_sources = (
            table.column(SOURCE_FILE).to_pylist() if SOURCE_FILE in table.column_names else [None] * table.num_rows
        )
        parent_paths_by_id = {
            current_id: (source, relative_path.split("/", 1)[0])
            for current_id, source, relative_path in zip(
                parent_table.column(CURRENT_ID).to_pylist(),
                parent_sources,
                parent_table.column(RELATIVE_PATH).to_pylist(),
                strict=True,
            )
            if isinstance(current_id, int) and isinstance(relative_path, str)
        }
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
        else:
            # Child rows are grouped by parent, in parent order (SPEC 7.2).
            row = next((row for row in range(1, len(parent_ids)) if parent_ids[row] < parent_ids[row - 1]), None)
            if row is not None:
                collector.error(
                    "parent_id", f"{label}{level}: rows must be grouped by parent in parent order (first row {row})"
                )
        prefix_ok = True
        children: dict[int, list[str]] = defaultdict(list)
        for parent_id, relative_path, source in zip(parent_ids, paths, child_sources, strict=True):
            if not isinstance(relative_path, str):
                prefix_ok = False
                continue
            parts = relative_path.split("/")
            parent_origin = parent_paths_by_id.get(parent_id)
            expected_depth = 2 + len(folder)
            if (
                len(parts) != expected_depth
                or not parts[0].isascii()
                or not parts[0].isdigit()
                or (len(parts[0]) > 1 and parts[0].startswith("0"))
                or (dataset.container != "tacocat" and int(parts[0]) >= sample_count)
                or parent_origin is None
                or parent_origin != (source, parts[0])
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
    # Folders and fixed files follow declaration order, and the files of a sequence their index (SPEC 5.2).
    positions = {
        item if kind == "folder" else item.identifier: position for position, (kind, item) in enumerate(entries)
    }
    problems = 0
    unordered = 0
    for parent in parent_ids:
        names = children.get(parent, [])
        seen = set(names)
        if len(seen) != len(names):
            problems += 1
            continue
        if fixed - seen:
            problems += 1
            continue
        keys = {name: (positions[name], 0) for name in fixed}
        remaining = seen - fixed
        for leaf in variables:
            indexes = []
            for name in list(remaining):
                index = leaf.match_index(name)
                if index is not None:
                    indexes.append(index)
                    keys[name] = (positions[leaf.identifier], index)
                    remaining.discard(name)
            if sorted(indexes) != list(range(len(indexes))) or not (leaf.minimum <= len(indexes) <= leaf.maximum):
                problems += 1
                break
        else:
            if remaining:
                problems += 1
            elif names != sorted(names, key=keys.__getitem__):
                unordered += 1
    if problems:
        collector.error(
            "structure", f"{label}{level}: {problems} parent(s) have children that do not match the contract"
        )
    if unordered:
        collector.error(
            "structure", f"{label}{level}: the rows of {unordered} parent(s) do not follow the structure order"
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
    reference = table_schema(contract, level, with_offsets=dataset.container != "folder")
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
            if field_.name != SAMPLE_ID and not field_.name.startswith("internal:"):
                expected_description = (field_.metadata or {}).get(b"description")
                actual_description = (actual_field.metadata or {}).get(b"description")
                if actual_description != expected_description:
                    collector.error(
                        "schema", f"{level}: column {field_.name!r} description does not match the contract"
                    )
            if not field_.nullable and column.null_count:
                collector.error("schema", f"{level}: column {field_.name!r} contains null values")
    _check_file_fields(contract, level, table, collector)
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
                # Path checks report null or non-string values.
                if not isinstance(relative_path, str):
                    continue
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


def _check_file_fields(contract: Contract, level: str, table: pa.Table, collector: _Collector) -> None:
    """A field that declares files holds values only on the rows of those files (SPEC 5.4)."""
    restricted = {
        name: spec.files
        for name, spec in contract.metadata[level].items()
        if spec.files is not None and name in table.column_names
    }
    if not restricted or RELATIVE_PATH not in table.column_names:
        return
    paths = table.column(RELATIVE_PATH).to_pylist()
    for name, files in restricted.items():
        leaves = [leaf for leaf in contract.leaves if leaf.declaration in files]
        rows = [
            row
            for row, valid in enumerate(table.column(name).is_valid().to_pylist())
            if valid
            and not (
                isinstance(paths[row], str)
                and any(leaf.match_index(paths[row].rsplit("/", 1)[-1]) is not None for leaf in leaves)
            )
        ]
        if rows:
            collector.error(
                "schema",
                f"{level}: column {name!r} has values on {len(rows)} rows outside its files {list(files)} "
                f"(first row {rows[0]}: {paths[rows[0]]!r})",
            )


# General purpose flag bits CoZIP forbids on every entry (CoZIP 5.1.6 and 5.1.7).
_FORBIDDEN_FLAGS = ((0, "encryption"), (3, "data descriptor"), (6, "strong encryption"), (13, "masked local header"))
_UTF8_NAMES = 0x800
_ZIP64_VALUE = 0xFFFFFFFF
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_END_RECORD = struct.Struct("<4s4H2LH")
# The CoZIP index payload follows the fixed 51-byte local header of __cozip__.
_INDEX_OFFSET = 51
_CHUNK_SIZE = 1 << 20


@dataclass(frozen=True)
class _LocalEntry:
    """A ZIP entry with the fields its local header repeats."""

    info: zipfile.ZipInfo
    name: bytes
    flags: int
    method: int
    crc: int
    compressed: int
    size: int
    payload: int

    @property
    def end(self) -> int:
        return self.payload + self.info.compress_size


def _local_entries(archive: zipfile.ZipFile, stream: BinaryIO) -> Iterator[_LocalEntry]:
    """Read the local header of every entry, in archive order."""
    for info in sorted(archive.infolist(), key=lambda item: item.header_offset):
        stream.seek(info.header_offset)
        header = stream.read(_LOCAL_HEADER.size)
        if len(header) < _LOCAL_HEADER.size or header[:4] != b"PK\x03\x04":
            raise ValueError(f"bad local header for {info.filename}")
        _, _, flags, method, _, _, crc, compressed, size, name_length, extra_length = _LOCAL_HEADER.unpack(header)
        name = stream.read(name_length)
        extra = stream.read(extra_length)
        if _ZIP64_VALUE in (compressed, size):
            size, compressed = _zip64_sizes(extra, size, compressed)
        payload = info.header_offset + _LOCAL_HEADER.size + name_length + extra_length
        yield _LocalEntry(info, name, flags, method, crc, compressed, size, payload)


def _zip64_sizes(extra: bytes, size: int, compressed: int) -> tuple[int, int]:
    """Replace the 0xFFFFFFFF sizes of a local header with its ZIP64 values."""
    position = 0
    while position + 4 <= len(extra):
        kind, length = struct.unpack_from("<HH", extra, position)
        data = extra[position + 4 : position + 4 + length]
        if kind == 1:
            values = list(struct.unpack_from(f"<{len(data) // 8}Q", data))
            if size == _ZIP64_VALUE and values:
                size = values.pop(0)
            if compressed == _ZIP64_VALUE and values:
                compressed = values.pop(0)
            break
        position += 4 + length
    return size, compressed


def _name_problem(name: str) -> str | None:
    """Return a CoZIP name violation, if any (CoZIP 5.1.8 and 5.3)."""
    if not name:
        return "must not be empty"
    if not all("\x01" <= char <= "\x7f" for char in name):
        return "must be ASCII without NUL bytes"
    if name.startswith("/") or (len(name) > 1 and name[0].isalpha() and name[1] == ":"):
        return "must be relative"
    if "\\" in name:
        return "must use '/' as the separator"
    if any(part in {".", ".."} for part in name.split("/")):
        return "must not contain '.' or '..' components"
    return None


def _entry_problems(entry: _LocalEntry) -> Iterator[str]:
    """Yield each CoZIP rule one entry breaks (CoZIP 5.1 and 5.3)."""
    info = entry.info
    if info.is_dir():
        yield "explicit directory entries are forbidden"
    else:
        problem = _name_problem(info.orig_filename)
        if problem is not None:
            yield f"entry names {problem}"
        if info.file_size == 0:
            yield "entries must not be empty"
    if info.compress_type != zipfile.ZIP_STORED:
        yield "entries must use STORE"
    elif info.compress_size != info.file_size:
        yield "STORE entries must have equal compressed and uncompressed sizes"
    flags = entry.flags | info.flag_bits
    for bit, meaning in _FORBIDDEN_FLAGS:
        if flags >> bit & 1:
            yield f"general purpose bit {bit} ({meaning}) is forbidden"
    central_name = info.orig_filename.encode("utf-8" if info.flag_bits & _UTF8_NAMES else "cp437")
    differences = [
        label
        for label, local, central in (
            ("name", entry.name, central_name),
            ("compression method", entry.method, info.compress_type),
            ("CRC-32", entry.crc, info.CRC),
            ("sizes", (entry.compressed, entry.size), (info.compress_size, info.file_size)),
        )
        if local != central
    ]
    if differences:
        yield f"local headers disagree with the central directory on the {', '.join(differences)}"


def _payload_crc(stream: BinaryIO, offset: int, size: int) -> int:
    stream.seek(offset)
    crc = 0
    while size > 0:
        chunk = stream.read(min(size, _CHUNK_SIZE))
        if not chunk:
            break
        crc = zlib.crc32(chunk, crc)
        size -= len(chunk)
    return crc


def _index_entries(payload: bytes) -> list[tuple[str, int, int]]:
    """Return the names, offsets and sizes a CoZIP index lists (CoZIP 7)."""
    (count,) = struct.unpack_from("<L", payload, 7)
    lengths = struct.unpack_from(f"<{count}H", payload, 11)
    position = 11 + 2 * count
    names = []
    for length in lengths:
        names.append(payload[position : position + length].decode("ascii"))
        position += length
    offsets = struct.unpack_from(f"<{count}Q", payload, position)
    sizes = struct.unpack_from(f"<{count}Q", payload, position + 8 * count)
    return list(zip(names, offsets, sizes, strict=True))


def _some(names: Sequence[str]) -> str:
    more = f" and {len(names) - 1} more" if len(names) > 1 else ""
    return f"{names[0]!r}{more}"


def _check_zip_end(archive: zipfile.ZipFile, stream: BinaryIO, size: int, collector: _Collector) -> None:
    """The archive is one segment and ends with its End of Central Directory record (CoZIP 5.1)."""
    stream.seek(size - _END_RECORD.size)
    signature, disk, central_disk, disk_entries, entries, _, _, _ = _END_RECORD.unpack(stream.read(_END_RECORD.size))
    if archive.comment:
        collector.error("zip", "archive comment must be empty")
    elif signature != b"PK\x05\x06":
        collector.error("zip", "the End of Central Directory record must end the archive")
    elif disk or central_disk or disk_entries != entries or any(info.volume for info in archive.infolist()):
        collector.error("zip", "split and multi-disk archives are forbidden")


def _check_index(
    stream: BinaryIO,
    entries: dict[str, _LocalEntry],
    expected: set[str],
    problems: dict[str, list[str]],
    collector: _Collector,
) -> None:
    """Check the CoZIP index against the entries it points to (CoZIP 7.4, 7.5 and 14.2)."""
    stream.seek(18)
    (length,) = struct.unpack("<L", stream.read(4))
    stream.seek(_INDEX_OFFSET)
    payload = stream.read(length)
    index = entries.get(INDEX_NAME)
    if index is not None and zlib.crc32(payload) != index.info.CRC:
        problems["payloads do not match their CRC-32"].append(INDEX_NAME)
    listed = _index_entries(payload)
    names = {name for name, _, _ in listed}
    missing = sorted(expected - names)
    if missing:
        collector.error("cozip", f"the CoZIP index does not list {missing}")
    unexpected = sorted(names - expected)
    if unexpected:
        collector.error("cozip", f"the CoZIP index lists entries that are not TACO priority files: {unexpected}")
    for name, offset, size in listed:
        entry = entries.get(name)
        if entry is None:
            collector.error("cozip", f"the CoZIP index lists {name!r}, which is not in the archive")
        elif (offset, size) != (entry.payload, entry.info.file_size):
            collector.error(
                "cozip",
                f"the CoZIP index gives {name!r} offset {offset} and size {size}, "
                f"but its payload has offset {entry.payload} and size {entry.info.file_size}",
            )


def _check_zip(dataset: DatasetView, collector: _Collector, *, check_data: bool) -> None:
    from .reader.inspect import levels

    # The native reader checks the CoZIP header, index layout, integrity hash and profile.
    try:
        levels(dataset.path)
        index_readable = True
    except Exception as exc:
        collector.error("cozip", str(exc))
        index_readable = False

    expected = {COLLECTION_FILENAME, *(f"{METADATA_DIR}/{level_to_filename(level)}" for level in dataset.levels)}
    expected_data = {row.archive_name: (row.offset, row.size) for row in dataset.iter_data_rows()} if check_data else {}
    seen_data: dict[str, tuple[int, int]] = {}
    problems: dict[str, list[str]] = defaultdict(list)
    readable = True
    try:
        with zipfile.ZipFile(dataset.path) as archive, dataset.path.open("rb") as stream:
            _check_zip_end(archive, stream, dataset.path.stat().st_size, collector)
            names = [info.filename for info in archive.infolist()]
            duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
            if duplicates:
                collector.error("zip", f"duplicate ZIP entries are forbidden (first: {duplicates[0]})")
            entries: dict[str, _LocalEntry] = {}
            order: list[str] = []
            end = 0
            for entry in _local_entries(archive, stream):
                name = entry.info.filename
                order.append(name)
                entries.setdefault(name, entry)
                for problem in _entry_problems(entry):
                    problems[problem].append(name)
                if entry.info.header_offset < end or entry.end > archive.start_dir:
                    problems["entries must not overlap each other or the central directory"].append(name)
                end = max(end, entry.end)
                # open_view read the priority files through zipfile, which checks
                # their CRC-32, and _check_index reads the index.
                if (
                    check_data
                    and name not in expected
                    and name != INDEX_NAME
                    and _payload_crc(stream, entry.payload, entry.info.compress_size) != entry.info.CRC
                ):
                    problems["payloads do not match their CRC-32"].append(name)
                if check_data and name.startswith(DATA_DIR + "/"):
                    seen_data[name] = (entry.payload, entry.info.file_size)
            if not order or order[0] != INDEX_NAME:
                collector.error("zip", "__cozip__ must be the first entry")
            block = order[-len(expected) :] if len(order) >= len(expected) else []
            if set(block) != expected:
                collector.error("zip", "priority files must form the final contiguous entry block")
            metadata_entries = {
                name.removeprefix(METADATA_DIR + "/") for name in names if name.startswith(METADATA_DIR + "/")
            }
            extra_metadata = sorted(metadata_entries - {level_to_filename(level) for level in dataset.levels})
            if extra_metadata:
                collector.error("metadata", f"unexpected files {extra_metadata}")
            if index_readable:
                _check_index(stream, entries, expected, problems, collector)
    except (zipfile.BadZipFile, ValueError, struct.error) as exc:
        collector.error("zip", f"cannot read ZIP structure: {exc}")
        readable = False
    for problem, entry_names in problems.items():
        collector.error("zip", f"{problem}: {_some(entry_names)}")

    if not readable or not check_data:
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
            collector.error("data", f"{len(extra)} files under DATA/ are not described by metadata (first: {extra[0]})")


def _check_tacocat(dataset: DatasetView, collector: _Collector, *, check_data: bool) -> None:
    sources = dataset.collection_json.get("taco:sources")
    entries: dict[str, dict[str, Any]] = {}
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
            entries = {item["file"]: item for item in partitions if isinstance(item.get("file"), str)}
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
    for level, table in dataset.tables.items():
        if SOURCE_FILE not in table.column_names:
            collector.error("source_file", f"{level}: TACOCAT tables need internal:source_file")
            continue
        unknown = sorted(set(table.column(SOURCE_FILE).to_pylist()) - set(listed)) if listed else []
        if unknown:
            collector.error(
                "source_file", f"{level}: rows reference partitions not listed in taco:sources: {unknown[:3]}"
            )
    _check_partitions(dataset, listed, entries, collector, check_data=check_data)


def _check_partitions(
    dataset: DatasetView,
    listed: list[str],
    entries: dict[str, dict[str, Any]],
    collector: _Collector,
    *,
    check_data: bool,
) -> None:
    """Validate every partition like a standalone ZIP and check the catalog against it (TACO 7.5)."""
    ranges = _partition_ranges(dataset, listed)
    fields = summary_types(dataset.contract)
    extents: list[Extent | None] = []
    summaries: dict[str, list[Any]] = defaultdict(list)
    for name in ranges:
        partition = _open_partition(dataset, name, collector)
        if partition is None:
            continue
        _check_dataset(partition, collector.labeled(f"partition {name!r}: "), check_data=check_data)
        _check_source_entry(entries[name], name, partition, collector)
        _check_partition_collection(dataset, partition, name, set(fields), collector)
        _check_partition_rows(dataset, partition, name, ranges[name], collector)
        extents.append(partition.collection.extent)
        for summary in fields:
            summaries[summary].append(partition.collection_json.get(summary))
    if ranges and len(extents) == len(ranges):
        _check_catalog_summaries(dataset, extents, fields, summaries, collector)


def _partition_ranges(dataset: DatasetView, listed: list[str]) -> dict[str, dict[str, tuple[int, int]]]:
    """Where each partition's rows sit in every catalog level: first row and row count."""
    ranges: dict[str, dict[str, tuple[int, int]]] = {name: {} for name in listed}
    for level, table in dataset.tables.items():
        if SOURCE_FILE not in table.column_names:
            continue
        counts = Counter(table.column(SOURCE_FILE).to_pylist())
        start = 0
        for name, positions in ranges.items():
            positions[level] = (start, counts[name])
            start += counts[name]
    return ranges


def _open_partition(dataset: DatasetView, name: str, collector: _Collector) -> DatasetView | None:
    try:
        normalized = normalize_relative_path(name, context="partition path")
    except ValueError as exc:
        collector.error("sources", str(exc))
        return None
    if not normalized.endswith(".zip"):
        collector.error("sources", f"partition path must end in .zip: {name!r}")
        return None
    path = dataset.path.parent / normalized
    if not path.is_file():
        collector.error("sources", f"partition {name!r} cannot be found")
        return None
    try:
        return open_view(path)
    except (TacoError, OSError, ValueError) as exc:
        collector.error("sources", f"partition {name!r} cannot be opened: {exc}")
        return None


def _check_source_entry(entry: dict[str, Any], name: str, partition: DatasetView, collector: _Collector) -> None:
    if entry.get("samples") != partition.sample_count:
        collector.error(
            "sources",
            f"the taco:sources sample count of {name!r} is {entry.get('samples')!r}, not {partition.sample_count}",
        )
    copied = {key: entry[key] for key in ("spatial", "temporal") if key in entry}
    try:
        matches = (Extent.from_any(copied) if copied else None) == partition.collection.extent
    except (TypeError, ValueError):
        matches = False
    if not matches:
        collector.error("sources", f"taco:sources spatial and temporal for {name!r} must copy the partition extent")


def _check_partition_collection(
    dataset: DatasetView, partition: DatasetView, name: str, summaries: set[str], collector: _Collector
) -> None:
    # Summaries, extent included, cover each partition's own rows (TACO 5.5).
    ignored = {"extent", "taco:sources", *summaries}
    catalog = {key: value for key, value in dataset.collection_json.items() if key not in ignored}
    own = {key: value for key, value in partition.collection_json.items() if key not in ignored}
    different = sorted(
        key for key in catalog.keys() | own.keys() if key not in catalog or key not in own or catalog[key] != own[key]
    )
    if different:
        collector.error("sources", f"the collection metadata of {name!r} differs from the catalog in {different}")


def _check_partition_rows(
    dataset: DatasetView,
    partition: DatasetView,
    name: str,
    ranges: dict[str, tuple[int, int]],
    collector: _Collector,
) -> None:
    """The catalog repeats the partition's rows in order, with ids counted across partitions."""
    contract = dataset.contract
    for level in contract.levels:
        table = dataset.tables.get(level)
        own = partition.tables.get(level)
        if table is None or own is None or level not in ranges:
            continue
        start, count = ranges[level]
        if count != own.num_rows:
            collector.error(
                "sources", f"{level}: the row count of {name!r} is {count} in the catalog, not {own.num_rows}"
            )
            continue
        rows = table.slice(start, count)
        if set(rows.column(SOURCE_FILE).to_pylist()) - {name}:
            collector.error("sources", f"{level}: the rows from {name!r} must be contiguous, in taco:sources order")
            continue
        shifts = {CURRENT_ID: start}
        if level != SAMPLE_LEVEL:
            folder = level_folder(level)
            parent = SAMPLE_LEVEL if level == CHILDREN_LEVEL else contract.level_of_folder(folder[:-1])
            shifts[PARENT_ID] = ranges.get(parent, (0, 0))[0]
        different = [
            column
            for column in own.column_names
            if column in rows.column_names
            and not _same_column(rows.column(column), own.column(column), shifts.get(column))
        ]
        if different:
            collector.error("sources", f"{level}: the rows from {name!r} differ from the partition in {different}")


def _same_column(catalog: pa.ChunkedArray, own: pa.ChunkedArray, shift: int | None) -> bool:
    if shift is not None:
        shifted = [value + shift if isinstance(value, int) else value for value in own.to_pylist()]
        return bool(catalog.to_pylist() == shifted)
    if catalog.equals(own):
        return True
    # Arrow never finds NaN equal to itself, so compare the values in Python.
    return bool(catalog.type == own.type) and _same_values(catalog.to_pylist(), own.to_pylist())


def _same_values(left: object, right: object) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (math.isnan(left) and math.isnan(right))
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_same_values(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_values(left[key], right[key]) for key in left)
    return left == right


def _check_catalog_summaries(
    dataset: DatasetView,
    extents: list[Extent | None],
    fields: dict[str, type[CollectionSummary]],
    summaries: dict[str, list[Any]],
    collector: _Collector,
) -> None:
    """The catalog combines the partition summaries the way taco.consolidate() does."""
    if dataset.collection.extent != Extent.union([extent for extent in extents if extent is not None]):
        collector.error("sources", "extent must be the union of the partition extents")
    for name, summary in fields.items():
        if name == "extent":
            continue
        try:
            merged = summary.merge(summaries[name])
        except Exception:
            # The partition checks report a summary that cannot be combined.
            continue
        if dataset.collection_json.get(name) != merged:
            collector.error("sources", f"{name} must combine the values of the partitions")
