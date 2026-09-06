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
_FORBIDDEN_IN_COMPONENT = frozenset('<>:"\\|?*[]')
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
        forbidden = forbidden - {"*", "[", "]"}
    bad = sorted(set(component) & forbidden)
    if bad:
        raise ContractError(f"{context} component {component!r} contains forbidden characters {bad}")
    if LEVEL_SEPARATOR in component:
        raise ContractError(f"{context} component {component!r} must not contain '__'")
    if component.endswith((" ", ".")):
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
    for index, part in enumerate(path.parts):
        validate_component(part, context=context, allow_glob=allow_glob and index == len(path.parts) - 1)
    return value


_VariableSequence = tuple[str, str, int]


def _variable_width(sequence: _VariableSequence, length: int) -> int | None:
    prefix, suffix, maximum = sequence
    width = length - len(prefix) - len(suffix)
    if width < 1 or width > len(str(maximum - 1)):
        return None
    if width > 1 and 10 ** (width - 1) >= maximum:
        return None
    return width


def _variable_token(sequence: _VariableSequence, width: int, position: int) -> tuple[str | None, int | None]:
    prefix, suffix, _ = sequence
    if position < len(prefix):
        return prefix[position], None
    digit = position - len(prefix)
    if digit < width:
        return None, digit
    return suffix[position - len(prefix) - width], None


def variable_sequences_overlap(first: _VariableSequence, second: _VariableSequence) -> bool:
    first_prefix, first_suffix, first_maximum = first
    second_prefix, second_suffix, second_maximum = second
    first_digits = len(str(first_maximum - 1))
    second_digits = len(str(second_maximum - 1))
    minimum = max(len(first_prefix) + len(first_suffix) + 1, len(second_prefix) + len(second_suffix) + 1)
    maximum = min(
        len(first_prefix) + len(first_suffix) + first_digits,
        len(second_prefix) + len(second_suffix) + second_digits,
    )
    for length in range(minimum, maximum + 1):
        first_width = _variable_width(first, length)
        second_width = _variable_width(second, length)
        if first_width is None or second_width is None:
            continue
        first_limit = str(first_maximum - 1)
        second_limit = str(second_maximum - 1)
        states = {(first_width == len(first_limit), second_width == len(second_limit))}
        for position in range(length):
            left_char, left_digit = _variable_token(first, first_width, position)
            right_char, right_digit = _variable_token(second, second_width, position)
            choices: tuple[str, ...]
            if left_char is not None and right_char is not None:
                choices = (left_char,) if left_char == right_char else ()
            elif left_char is not None:
                choices = (left_char,) if left_char.isdigit() else ()
            elif right_char is not None:
                choices = (right_char,) if right_char.isdigit() else ()
            else:
                choices = tuple("0123456789")
            if left_digit == 0 and first_width > 1:
                choices = tuple(value for value in choices if value != "0")
            if right_digit == 0 and second_width > 1:
                choices = tuple(value for value in choices if value != "0")

            next_states = set()
            for first_tight, second_tight in states:
                for value in choices:
                    next_first = first_tight
                    next_second = second_tight
                    if left_digit is not None and first_tight:
                        limit = first_limit[left_digit]
                        if value > limit:
                            continue
                        next_first = value == limit
                    if right_digit is not None and second_tight:
                        limit = second_limit[right_digit]
                        if value > limit:
                            continue
                        next_second = value == limit
                    next_states.add((next_first, next_second))
            states = next_states
            if not states:
                break
        if states:
            return True
    return False


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


def level_folder(level: str) -> tuple[str, ...]:
    if level in {"sample", "children"}:
        return ()
    return tuple(level.split("/")[1:])


def sanitize_filename(value: str) -> str:
    """Make a partition value safe to use in a file name."""
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
    if not isinstance(value, str):
        raise TypeError("size must be an int or a string")
    match = _SIZE_PATTERN.fullmatch(value)
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
