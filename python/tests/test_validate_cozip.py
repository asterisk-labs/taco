from __future__ import annotations

import io
import struct
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest

import taco

ENTRY = "DATA/0/mask.tif"
INDEX_OFFSET = 51
HASH_WINDOW = 32768

Edit = Callable[[bytearray], object]


def rehash(data: bytearray) -> None:
    """Recompute the CoZIP integrity hash, so that only the edit under test is wrong."""
    (index_size,) = struct.unpack_from("<I", data, 18)
    index_end = INDEX_OFFSET + index_size
    value = 0xCBF29CE484222325
    for byte in bytes(data[INDEX_OFFSET:index_end] + data[max(len(data) - HASH_WINDOW, index_end) :]):
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    struct.pack_into("<Q", data, 43, value)


def headers(data: bytearray, name: str) -> tuple[int, int]:
    """The offsets of the local header and of the central directory record of an entry."""
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
        local = archive.getinfo(name).header_offset
        position = archive.start_dir
    while data[position : position + 4] == b"PK\x01\x02":
        name_length, extra_length, comment_length = struct.unpack_from("<3H", data, position + 28)
        if data[position + 46 : position + 46 + name_length] == name.encode():
            return local, position
        position += 46 + name_length + extra_length + comment_length
    raise KeyError(name)


def payload(data: bytearray, name: str) -> int:
    local, _ = headers(data, name)
    name_length, extra_length = struct.unpack_from("<HH", data, local + 26)
    return local + 30 + name_length + extra_length


def tampered(archive: Path, tmp_path: Path, edit: Edit) -> Path:
    data = bytearray(archive.read_bytes())
    edit(data)
    rehash(data)
    path = tmp_path / "tampered.zip"
    path.write_bytes(data)
    return path


def set_flag(bit: int) -> Edit:
    def edit(data: bytearray) -> None:
        local, central = headers(data, ENTRY)
        for offset in (local + 6, central + 8):
            (flags,) = struct.unpack_from("<H", data, offset)
            struct.pack_into("<H", data, offset, flags | 1 << bit)

    return edit


def rename(new: str) -> Edit:
    def edit(data: bytearray) -> None:
        local, central = headers(data, ENTRY)
        data[local + 30 : local + 30 + len(ENTRY)] = new.encode()
        data[central + 46 : central + 46 + len(ENTRY)] = new.encode()

    return edit


def rename_local(data: bytearray) -> None:
    local, _ = headers(data, ENTRY)
    data[local + 30 : local + 30 + len(ENTRY)] = ENTRY.replace("mask", "MASK").encode()


def flip_payload(data: bytearray) -> None:
    data[payload(data, ENTRY)] ^= 1


def swap_index_entries(data: bytearray) -> None:
    # Swap where two metadata files are said to be and keep the ZIP itself consistent.
    (size,) = struct.unpack_from("<I", data, 18)
    index = INDEX_OFFSET
    (count,) = struct.unpack_from("<I", data, index + 7)
    lengths = struct.unpack_from(f"<{count}H", data, index + 11)
    position = index + 11 + 2 * count
    names = []
    for length in lengths:
        names.append(bytes(data[position : position + length]).decode())
        position += length
    first = names.index("METADATA/children__before.parquet")
    second = names.index("METADATA/children__after.parquet")
    for start in (position, position + 8 * count):
        values = list(struct.unpack_from(f"<{count}Q", data, start))
        values[first], values[second] = values[second], values[first]
        struct.pack_into(f"<{count}Q", data, start, *values)
    crc = zlib.crc32(bytes(data[index : index + size]))
    local, central = headers(data, "__cozip__")
    struct.pack_into("<I", data, local + 14, crc)
    struct.pack_into("<I", data, central + 16, crc)


def local_field(offset: int, value: bytes) -> Edit:
    def edit(data: bytearray) -> None:
        local, _ = headers(data, ENTRY)
        data[local + offset : local + offset + len(value)] = value

    return edit


def end_record(offset: int, value: int) -> Edit:
    def edit(data: bytearray) -> None:
        struct.pack_into("<H", data, len(data) - 22 + offset, value)

    return edit


def test_valid_archive_passes_the_cozip_checks(archive: Path, tmp_path: Path) -> None:
    path = tampered(archive, tmp_path, lambda data: None)
    assert taco.validate(path).ok
    assert taco.validate(path, check_data=False).ok


@pytest.mark.parametrize(
    ("edit", "code", "message"),
    [
        pytest.param(swap_index_entries, "cozip", "the CoZIP index gives 'METADATA/children__", id="index-offsets"),
        pytest.param(set_flag(0), "zip", "bit 0 (encryption)", id="flag-encryption"),
        pytest.param(set_flag(3), "zip", "bit 3 (data descriptor)", id="flag-data-descriptor"),
        pytest.param(set_flag(6), "zip", "bit 6 (strong encryption)", id="flag-strong-encryption"),
        pytest.param(set_flag(13), "zip", "bit 13 (masked local header)", id="flag-masked-header"),
        pytest.param(rename_local, "zip", "central directory on the name", id="local-name"),
        pytest.param(local_field(8, b"\x08\x00"), "zip", "on the compression method", id="local-method"),
        pytest.param(local_field(14, bytes(4)), "zip", "on the CRC-32", id="local-crc"),
        pytest.param(local_field(18, struct.pack("<II", 1, 1)), "zip", "on the sizes", id="local-sizes"),
        pytest.param(flip_payload, "zip", f"payloads do not match their CRC-32: {ENTRY!r}", id="payload-crc"),
        pytest.param(rename("DATA/./mask.tif"), "zip", "'.' or '..' components", id="dot-component"),
        pytest.param(lambda data: data.extend(bytes(8)), "zip", "must end the archive", id="trailing-bytes"),
        pytest.param(end_record(4, 1), "zip", "multi-disk", id="multi-disk"),
    ],
)
def test_cozip_rules_are_enforced(archive: Path, tmp_path: Path, edit: Edit, code: str, message: str) -> None:
    report = taco.validate(tampered(archive, tmp_path, edit))
    assert any(issue.code == code and message in issue.message for issue in report.errors), report


def test_swapped_index_is_the_only_problem(archive: Path, tmp_path: Path) -> None:
    report = taco.validate(tampered(archive, tmp_path, swap_index_entries))
    assert [issue.code for issue in report.issues] == ["cozip", "cozip"]


def test_payload_crc_is_a_data_check(archive: Path, tmp_path: Path) -> None:
    path = tampered(archive, tmp_path, flip_payload)
    assert not taco.validate(path).ok
    assert taco.validate(path, check_data=False).ok


def test_zip64_local_headers_are_resolved(archive: Path, tmp_path: Path) -> None:
    # Large entries keep their sizes in a ZIP64 extra field of the local header.
    rebuilt = tmp_path / "zip64.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for info in source.infolist():
            with target.open(info.filename, "w", force_zip64=True) as stream:
                stream.write(source.read(info))
    report = taco.validate(rebuilt)
    assert not any("local headers" in issue.message for issue in report.issues), report
