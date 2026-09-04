from __future__ import annotations

import re
from pathlib import PurePosixPath

import pyarrow as pa

from ..errors import ContractError

# TACO spec 7.2. Users may not declare fields with this prefix.
CURRENT_ID = "internal:current_id"
PARENT_ID = "internal:parent_id"
RELATIVE_PATH = "internal:relative_path"
OFFSET = "internal:offset"
SIZE = "internal:size"
SOURCE_FILE = "internal:source_file"

ID_TYPE = pa.uint64()
OFFSET_TYPE = pa.uint64()

COLLECTION_FILENAME = "COLLECTION.json"
DATA_DIR = "DATA"
METADATA_DIR = "METADATA"
TACOCAT_DIR = ".tacocat"
LEVEL_SEPARATOR = "__"

# Windows forbids these in file names; TACO reserves ':' (namespaces) and
# '__' (level separator) on top of that. '/' is the path separator.
_FORBIDDEN_IN_COMPONENT = frozenset('<>:"\\|?*')
_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?I?B?)\s*$", re.I)
_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
}


def is_ascii(text: str) -> bool:
    return all(0x20 <= ord(char) < 0x7F for char in text)


def validate_component(component: str, *, context: str, allow_glob: bool = False) -> None:
    """Check one folder or file name against TACO 5.3 and cozip 5.3."""
    if not component or component in {".", ".."}:
        raise ContractError(f"invalid {context} component {component!r}")
    if not is_ascii(component):
        raise ContractError(f"{context} component {component!r} must be printable ASCII")
    forbidden = _FORBIDDEN_IN_COMPONENT
    if allow_glob:
        forbidden = forbidden - {"*"}
    bad = sorted(set(component) & forbidden)
    if bad:
        raise ContractError(f"{context} component {component!r} contains forbidden characters {bad}")
    if LEVEL_SEPARATOR in component:
        raise ContractError(f"{context} component {component!r} must not contain '__'")
    if component.endswith(" ") or component.endswith("."):
        raise ContractError(f"{context} component {component!r} must not end with a space or dot")


def normalize_relative_path(value: str, *, context: str, allow_glob: bool = False) -> str:
    """Return ``value`` if it is a normalized, relative, portable POSIX path."""
    if not isinstance(value, str) or not value:
        raise ContractError(f"{context} must be a non-empty string")
    if "\\" in value:
        raise ContractError(f"{context} must use '/' as separator: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or value.endswith("/"):
        raise ContractError(f"{context} must be a normalized relative POSIX path: {value!r}")
    for part in path.parts:
        validate_component(part, context=context, allow_glob=allow_glob)
    return value


def validate_field_name(name: str, *, context: str) -> None:
    if not isinstance(name, str) or not name:
        raise ContractError(f"{context} field name must be a non-empty string")
    if name.startswith("internal:"):
        raise ContractError(f"{context} field {name!r} uses the reserved 'internal:' prefix")
    if LEVEL_SEPARATOR in name:
        raise ContractError(f"{context} field {name!r} must not contain '__'")
    if "/" in name or "\x00" in name:
        raise ContractError(f"{context} field {name!r} contains forbidden characters")


def level_to_filename(level: str) -> str:
    return level.replace("/", LEVEL_SEPARATOR) + ".parquet"


def filename_to_level(filename: str) -> str:
    stem = filename[: -len(".parquet")] if filename.endswith(".parquet") else filename
    return stem.replace(LEVEL_SEPARATOR, "/")


def level_folder(level: str) -> tuple[str, ...]:
    """Return the sample-relative folder addressed by a metadata level key."""
    if level in {"collection", "sample"}:
        return ()
    return tuple(level.split("/")[1:])


def sanitize_filename(value: str) -> str:
    """Make ``value`` safe for use inside a file name (legacy tacotoolbox rule)."""
    sanitized = re.sub(r'[/\\:*?"<>|\']', "_", str(value))
    sanitized = re.sub(r"[_\s]+", "_", sanitized).strip("_")
    return sanitized or "group"


def parse_size(value: int | str) -> int:
    """Parse ``"4GB"``-style sizes into bytes (binary units)."""
    if isinstance(value, bool):
        raise ValueError("size must be an int or a string")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("size must be positive")
        return value
    match = _SIZE_PATTERN.match(str(value))
    if match is None:
        raise ValueError(f"invalid size {value!r}; use e.g. '4GB', '512MB', '1024KB'")
    number = float(match.group(1))
    unit = match.group(2).upper()
    if unit not in _SIZE_UNITS:
        raise ValueError(f"invalid size unit in {value!r}")
    result = int(number * _SIZE_UNITS[unit])
    if result <= 0:
        raise ValueError("size must be positive")
    return result
