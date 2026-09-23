from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pyarrow as pa

from ..metadata._base import Extension, ExtensionContext

_BAND_STATS = pa.struct(
    [
        pa.field("minimum", pa.float64(), nullable=True),
        pa.field("maximum", pa.float64(), nullable=True),
        pa.field("mean", pa.float64(), nullable=True),
        pa.field("stddev", pa.float64(), nullable=True),
        pa.field("valid_count", pa.int64(), nullable=False),
        pa.field("nodata_count", pa.int64(), nullable=False),
    ]
)


def _band_stats(array: np.ndarray, nodata: float | int | None) -> list[dict[str, float | int | None]]:
    if array.ndim == 3:
        bands = (array[index] for index in range(array.shape[0]))
    elif array.ndim == 4:
        bands = (array[:, index, ...] for index in range(array.shape[1]))
    else:
        raise ValueError(f"Rumi arrays must have shape (B,Y,X) or (T,B,Y,X), got {array.shape}")

    result: list[dict[str, float | int | None]] = []
    for band in bands:
        valid = np.isfinite(band)
        if nodata is not None:
            valid &= band != nodata
        values = band[valid]
        count = int(values.size)
        missing = int(band.size - count)
        if count:
            result.append(
                {
                    "minimum": float(values.min()),
                    "maximum": float(values.max()),
                    "mean": float(values.mean(dtype=np.float64)),
                    "stddev": float(values.std(dtype=np.float64)),
                    "valid_count": count,
                    "nodata_count": missing,
                }
            )
        else:
            result.append(
                {
                    "minimum": None,
                    "maximum": None,
                    "mean": None,
                    "stddev": None,
                    "valid_count": 0,
                    "nodata_count": missing,
                }
            )
    return result


def _source(value: Path | None) -> Path:
    if value is None:
        raise ValueError("the Rumi extension requires one local asset for every metadata row")
    if value.suffix.lower() != ".rumi":
        raise ValueError(f"the Rumi extension only accepts .rumi assets, got {value.name!r}")
    return value


@dataclass(frozen=True)
class Rumi(Extension):
    """Inspect local ``.rumi`` assets during ``writer.run()``."""

    stats: bool = False
    nodata: float | int | None = None

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample", "asset"})

    def __post_init__(self) -> None:
        if not isinstance(self.stats, bool):
            raise TypeError("stats must be a boolean")
        if self.nodata is not None:
            if isinstance(self.nodata, bool) or not isinstance(self.nodata, int | float):
                raise TypeError("nodata must be a number or None")
            if not math.isfinite(self.nodata):
                raise ValueError("nodata must be finite")

    @property
    def requires(self) -> tuple[str, ...]:
        return ()

    @property
    def fields(self) -> pa.Schema:
        fields = [
            pa.field(
                "header",
                pa.binary(),
                nullable=False,
                metadata={b"description": b"Canonical external Rumi header"},
            )
        ]
        if self.stats:
            fields.append(
                pa.field(
                    "stats",
                    pa.list_(pa.field("item", _BAND_STATS, nullable=False)),
                    nullable=False,
                    metadata={b"description": b"Per-band statistics over decoded valid samples"},
                )
            )
        return pa.schema(fields)

    def configuration(self) -> Mapping[str, Any]:
        return {"stats": self.stats, "nodata": self.nodata}

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        # Validate the row-to-asset contract before importing the optional
        # runtime so malformed inputs report their own error deterministically.
        sources = tuple(_source(value) for value in context.assets)
        try:
            import rumi
        except ImportError as exc:
            raise ImportError("the Rumi extension requires 'taco-eo[rumi]'") from exc

        headers = []
        statistics = []
        for source in sources:
            metadata = rumi.info(source=source)
            headers.append(metadata.header)
            if self.stats:
                statistics.append(_band_stats(np.asarray(rumi.read(source, metadata.header)), self.nodata))
        result: dict[str, Sequence[Any]] = {"header": headers}
        if self.stats:
            result["stats"] = statistics
        return result


__all__ = ["Rumi"]
