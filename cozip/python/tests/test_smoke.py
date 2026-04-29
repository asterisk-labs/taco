"""Smoke + roundtrip tests for the cozip Python binding."""

import io
import struct
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import cozip

# Optional geo stack — only needed for the geoparquet test.
try:
    import geopandas as gpd
    from shapely.geometry import Point
    HAS_GEO = True
except ImportError:
    HAS_GEO = False


# ---------------------------------------------------------------- bindings

def test_library_loads():
    """The shared library loads and exposes the expected symbols."""
    assert cozip.lib is not None
    for sym in (
        "cozip_plan",
        "cozip_index_payload_size",
        "cozip_build_index_payload",
        "cozip_write_archive",
        "cozip_patch_integrity_hash",
    ):
        assert hasattr(cozip.lib, sym), f"missing symbol: {sym}"


def test_error_struct():
    """cozip_error_t can be instantiated and read back."""
    err = cozip.ffi.new("cozip_error_t*")
    err.code = 42
    cozip.ffi.memmove(err.message, b"oops\x00", 5)
    assert err.code == 42
    assert cozip.ffi.string(err.message).decode() == "oops"


# ---------------------------------------------------------------- helpers

def _make_payload(seed: int, size: int) -> bytes:
    """Deterministic byte payload — same seed always yields same bytes."""
    return bytes([(seed + i) & 0xFF for i in range(size)])


# ---------------------------------------------------------------- roundtrip

def test_roundtrip(tmp_path: Path):
    """End-to-end: create a .cozip archive and validate every layer.

    Validates:
      1. archive exists and clears the spec minimum size (>= 32819)
      2. zipfile sees a valid ZIP with __cozip__ first and __metadata__ present
      3. LFH layout for the __cozip__ entry (filename len, extra len, 0xCA0C)
      4. integrity hash (bytes 43..50) is non-zero
      5. CZIP magic at byte 51
      6. cozip index lists 4 entries (3 users + __metadata__)
      7. user payloads round-trip byte-for-byte through zipfile
      8. __metadata__.parquet has the expected schema and offsets
    """
    sizes = [12000, 13000, 14000]
    src_files: list[Path] = []
    src_payloads: list[bytes] = []
    for i, sz in enumerate(sizes):
        p = tmp_path / f"src_{i}.bin"
        payload = _make_payload(0xA0 + i, sz)
        p.write_bytes(payload)
        src_files.append(p)
        src_payloads.append(payload)

    arc_names = [f"data/file_{i}.bin" for i in range(3)]
    table = pa.table({
        "name": arc_names,
        "path": [str(p) for p in src_files],
    })

    out = tmp_path / "out.cozip"
    returned = cozip.create(out, table)

    # 1. exists + size >= minimum
    assert Path(returned).exists()
    assert out.exists()
    archive_size = out.stat().st_size
    assert archive_size >= 32819, f"archive too small: {archive_size}"

    # 2. valid ZIP, first entry __cozip__, __metadata__ present
    with zipfile.ZipFile(out, "r") as z:
        names = z.namelist()
        infos = z.infolist()
    assert names[0] == "__cozip__", f"first entry is {names[0]!r}"
    assert "__metadata__" in names, "__metadata__ entry missing"
    for arc in arc_names:
        assert arc in names, f"missing entry: {arc}"

    cozip_info = next(i for i in infos if i.filename == "__cozip__")
    assert cozip_info.compress_type == zipfile.ZIP_STORED

    # 3. inspect raw bytes for the __cozip__ LFH
    raw = out.read_bytes()
    assert raw[:4] == b"PK\x03\x04", "missing local file header signature"

    fname_len = struct.unpack_from("<H", raw, 26)[0]
    extra_len = struct.unpack_from("<H", raw, 28)[0]
    assert fname_len == 9, f"__cozip__ name length: {fname_len}"
    assert extra_len == 12, f"0xCA0C extra length: {extra_len}"
    assert raw[30:39] == b"__cozip__"

    extra_id = struct.unpack_from("<H", raw, 39)[0]
    assert extra_id == 0xCA0C, f"unexpected extra id: 0x{extra_id:04X}"
    assert struct.unpack_from("<H", raw, 41)[0] == 8

    # 4. hash patched (non-zero)
    assert raw[43:51] != b"\x00" * 8, "integrity hash was not patched"

    # 5. CZIP magic at byte 51
    assert raw[51:55] == b"CZIP", f"bad magic: {raw[51:55]!r}"

    # 6. index header: version, profile, n_entries
    version = struct.unpack_from("<H", raw, 55)[0]
    profile = raw[57]
    n_index = struct.unpack_from("<I", raw, 58)[0]
    assert version == 1
    assert profile == 1, f"profile must be FLAT (1), got {profile}"
    assert n_index == 4, f"index count: expected 4 (3 users + meta), got {n_index}"

    # 7. user payloads round-trip via zipfile
    with zipfile.ZipFile(out, "r") as z:
        for arc, expected in zip(arc_names, src_payloads):
            assert z.read(arc) == expected, f"payload mismatch for {arc}"

    # 8. __metadata__.parquet schema and content
    with zipfile.ZipFile(out, "r") as z:
        meta_bytes = z.read("__metadata__")
    meta = pq.read_table(io.BytesIO(meta_bytes))

    assert meta.column_names[:3] == ["name", "offset", "size"]
    assert "path" not in meta.column_names, "path must not appear in __metadata__"
    assert "in_index" not in meta.column_names, "in_index must not appear in __metadata__"
    assert len(meta) == 3, f"expected 3 rows in __metadata__, got {len(meta)}"

    # offsets + sizes in the parquet must point to the actual user payloads
    meta_names = meta.column("name").to_pylist()
    meta_offsets = meta.column("offset").to_pylist()
    meta_sizes = meta.column("size").to_pylist()
    for arc, expected in zip(arc_names, src_payloads):
        idx = meta_names.index(arc)
        off = int(meta_offsets[idx])
        sz = int(meta_sizes[idx])
        assert raw[off:off + sz] == expected, f"offset/size wrong for {arc}"


def test_roundtrip_in_index_false(tmp_path: Path):
    """Entries with in_index=False are written but not listed in the index.

    The cozip index still gets __metadata__ entry, so n_index = 1 + 1 = 2
    when only one user is in_index=True.
    """
    src = tmp_path / "small.bin"
    big = tmp_path / "big.bin"
    src.write_bytes(_make_payload(0x10, 100))
    big.write_bytes(_make_payload(0x20, 33000))  # alone clears the 32 KiB minimum

    table = pa.table({
        "name":     ["a.bin", "b.bin"],
        "path":     [str(src), str(big)],
        "in_index": [True, False],
    })

    out = tmp_path / "out.cozip"
    cozip.create(out, table)

    raw = out.read_bytes()
    n_index = struct.unpack_from("<I", raw, 58)[0]
    assert n_index == 2, f"expected a.bin + __metadata__ in index, got {n_index}"

    with zipfile.ZipFile(out, "r") as z:
        names = z.namelist()
    assert "a.bin" in names
    assert "b.bin" in names
    assert "__metadata__" in names

    # The metadata parquet must NOT list b.bin (in_index=False).
    with zipfile.ZipFile(out, "r") as z:
        meta_bytes = z.read("__metadata__")
    meta = pq.read_table(io.BytesIO(meta_bytes))
    meta_names = meta.column("name").to_pylist()
    assert meta_names == ["a.bin"], f"expected only a.bin in metadata, got {meta_names}"


# ---------------------------------------------------------------- geoparquet

@pytest.mark.skipif(not HAS_GEO, reason="geopandas/shapely not installed")
def test_geoparquet_metadata_roundtrip(tmp_path: Path):
    """A GeoDataFrame as input survives as a GeoParquet inside __metadata__.

    Validates:
      1. user-supplied geometry + CRS reach the metadata parquet
      2. extra user columns flow through unchanged
      3. offsets in the metadata parquet point at the correct user payloads
      4. GeoPandas can read __metadata__ directly from the zip
    """
    n = 3
    src_files: list[Path] = []
    src_payloads: list[bytes] = []
    for i in range(n):
        p = tmp_path / f"src_{i}.bin"
        payload = _make_payload(0xA0 + i, 12000 + i * 1000)
        p.write_bytes(payload)
        src_files.append(p)
        src_payloads.append(payload)

    arc_names = [f"data/file_{i}.bin" for i in range(n)]
    countries = ["Peru", "Chile", "Brazil"]
    elevations = [3000, 4500, 1200]
    points = [
        Point(-77.0, -12.0),  # Lima
        Point(-70.7, -33.4),  # Santiago
        Point(-47.9, -15.7),  # Brasilia
    ]

    gdf = gpd.GeoDataFrame(
        {
            "name": arc_names,
            "path": [str(p) for p in src_files],
            "country": countries,
            "elevation": elevations,
            "geometry": points,
        },
        crs="EPSG:4326",
    )

    # GDF -> parquet -> pa.Table preserves the b"geo" schema metadata that
    # GeoParquet readers (incl. GeoPandas) look for.
    in_parquet = tmp_path / "input.parquet"
    gdf.to_parquet(in_parquet)
    table = pq.read_table(in_parquet)

    assert table.schema.metadata is not None
    assert b"geo" in table.schema.metadata, \
        "input table should carry the b'geo' schema metadata"

    # Build the cozip.
    out = tmp_path / "out.cozip"
    cozip.create(out, table)
    assert out.exists()

    raw = out.read_bytes()

    # Read __metadata__ straight out of the zip and parse with GeoPandas.
    with zipfile.ZipFile(out, "r") as z:
        assert "__metadata__" in z.namelist()
        meta_bytes = z.read("__metadata__")

    meta_gdf = gpd.read_parquet(io.BytesIO(meta_bytes))

    # 1. CRS preserved
    assert meta_gdf.crs is not None, "CRS lost in roundtrip"
    assert meta_gdf.crs.to_epsg() == 4326

    # Geometry round-trip
    assert "geometry" in meta_gdf.columns
    assert set(meta_gdf.geometry.geom_type) == {"Point"}
    assert len(meta_gdf) == n

    # 2. Extra columns preserved, writer-private columns dropped
    assert "country" in meta_gdf.columns
    assert "elevation" in meta_gdf.columns
    assert "path" not in meta_gdf.columns
    assert "in_index" not in meta_gdf.columns

    # Writer-added columns
    assert "name" in meta_gdf.columns
    assert "offset" in meta_gdf.columns
    assert "size" in meta_gdf.columns

    # User column values come back intact
    by_name = meta_gdf.set_index("name")
    for arc, country, elev, pt in zip(arc_names, countries, elevations, points):
        row = by_name.loc[arc]
        assert row["country"] == country
        assert int(row["elevation"]) == elev
        assert row["geometry"].equals(pt), f"geometry mismatch for {arc}"

    # 3. offsets/sizes resolve to the original user payloads
    for arc, expected in zip(arc_names, src_payloads):
        row = by_name.loc[arc]
        off = int(row["offset"])
        sz = int(row["size"])
        assert raw[off:off + sz] == expected, f"offset/size wrong for {arc}"


# ---------------------------------------------------------------- negatives

def test_create_rejects_empty(tmp_path: Path):
    """create() refuses to build a 0-entry archive."""
    out = tmp_path / "empty.cozip"
    with pytest.raises(ValueError, match="empty"):
        cozip.create(out, pa.table({"name": [], "path": []}))


def test_create_rejects_missing_source(tmp_path: Path):
    """Missing source file is caught before any C call."""
    out = tmp_path / "out.cozip"
    table = pa.table({
        "name": ["does/not/exist.bin"],
        "path": [str(tmp_path / "nope.bin")],
    })
    with pytest.raises(FileNotFoundError):
        cozip.create(out, table)


def test_create_rejects_reserved_name(tmp_path: Path):
    """User cannot use names the writer reserves for itself."""
    src = tmp_path / "src.bin"
    src.write_bytes(_make_payload(0x33, 33000))
    table = pa.table({
        "name": ["__metadata__"],
        "path": [str(src)],
    })
    with pytest.raises(ValueError, match="reserved"):
        cozip.create(tmp_path / "out.cozip", table)


def test_create_rejects_duplicate_names(tmp_path: Path):
    """Duplicate names within one archive are rejected upfront."""
    src1 = tmp_path / "a.bin"
    src2 = tmp_path / "b.bin"
    src1.write_bytes(_make_payload(0x10, 100))
    src2.write_bytes(_make_payload(0x20, 33000))
    table = pa.table({
        "name": ["dupe.bin", "dupe.bin"],
        "path": [str(src1), str(src2)],
    })
    with pytest.raises(ValueError, match="duplicate"):
        cozip.create(tmp_path / "out.cozip", table)