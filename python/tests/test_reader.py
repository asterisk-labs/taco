from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from taco.reader import collection, contract, levels, profile, read, sql, structure
from taco.reader.engine import connect


def test_reader_api(archive: Path) -> None:
    assert profile(archive) == "taco"
    assert structure(archive)[0] == "before/B02.tif"
    metadata_levels = levels(archive)
    assert metadata_levels[:2] == ["collection", "sample"]
    assert set(metadata_levels[2:]) == {"sample/before", "sample/after"}
    assert collection(archive)["id"] == "tiny-change"
    assert contract(archive).num_rows > 0

    table = read(archive, idx=(1, 3), files=("mask.tif",))
    assert table.num_rows == 2
    assert "mask.tif" in table.column_names
    assert "read_parquet" in sql(archive, idx=1)


@pytest.mark.parametrize("idx", [True, [1], [1, 2, 3], [0, True]])
def test_reader_rejects_invalid_indexes(archive: Path, idx) -> None:
    with pytest.raises(TypeError, match="idx"):
        read(archive, idx=idx)


def test_reader_uses_a_connection_per_thread(archive: Path) -> None:
    main_connection = connect()
    barrier = Barrier(2)

    def load(_: int) -> tuple[int, int]:
        connection_id = id(connect())
        barrier.wait()
        return connection_id, read(archive).num_rows

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(load, range(2)))

    assert all(rows == 4 for _, rows in results)
    assert len({connection_id for connection_id, _ in results}) == 2
    assert all(connection_id != id(main_connection) for connection_id, _ in results)
