from __future__ import annotations

import pytest

from taco._parquet import DEFAULT_PARQUET_OPTIONS, parquet_writer_options


def test_parquet_options_override_defaults_without_mutating_them() -> None:
    options = parquet_writer_options({"compression": "snappy"})
    assert options["compression"] == "snappy"
    assert DEFAULT_PARQUET_OPTIONS["compression"] == "zstd"


def test_row_group_size_has_one_public_argument() -> None:
    with pytest.raises(ValueError, match="writer argument"):
        parquet_writer_options({"row_group_size": 100})
