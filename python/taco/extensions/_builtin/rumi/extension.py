from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypeGuard

import numpy as np
import pyarrow as pa

from ....contract.extension import Extension, ExtensionContext
from ....contract.naming import RUMI_NAMESPACE, RUMI_STATISTIC
from ....contract.structure import Leaf, parse_leaf
from ....errors import SampleError

# Statistics included by stats=True.
STATISTICS = ("minimum", "maximum", "mean", "stddev", "p2", "p98")

_StatisticSelection = bool | str | Sequence[str]
_NormalizedFileStatistics = tuple[tuple[str, tuple[str, ...]], ...]

_REDUCERS: dict[str, Callable[[np.ndarray], Any]] = {
    "minimum": lambda values: values.min(),
    "maximum": lambda values: values.max(),
    "mean": lambda values: values.mean(dtype=np.float64),
    "stddev": lambda values: values.std(dtype=np.float64),
}
_PERCENTILES = {"p2": 2, "p98": 98}
# Integer ranges narrower than this are counted instead of sorted.
_COUNTED_SPAN = 1 << 16
_LABELS = {
    "minimum": "Minimum",
    "maximum": "Maximum",
    "mean": "Mean",
    "stddev": "Population standard deviation",
    "p2": "2nd percentile",
    "p98": "98th percentile",
}


@dataclass(frozen=True)
class _Statistic:
    name: str
    kind: str
    time: int | None
    band: int | None

    @property
    def description(self) -> str:
        scope = "all valid values"
        if self.band is not None or self.time is not None:
            scope = "the valid values"
            if self.band is not None:
                scope += f" in band {self.band}"
            if self.time is not None:
                scope += f" at time step {self.time}"
        return f"{_LABELS[self.kind]} of {scope}"


def _statistic(name: object) -> _Statistic:
    if not isinstance(name, str):
        raise TypeError(f"Rumi statistic names must be strings, got {type(name).__name__}")
    match = RUMI_STATISTIC.fullmatch(name)
    if match is None:
        raise ValueError(
            f"unknown Rumi statistic {name!r}; use {', '.join(STATISTICS)}, "
            "optionally followed by _b<band>, _t<time> or _t<time>_b<band>"
        )
    kind, band, time, time_band = match.groups()
    band = band if band is not None else time_band
    return _Statistic(name, kind, None if time is None else int(time), None if band is None else int(band))


def _statistics(value: object, *, allow_mapping: bool = False) -> tuple[_Statistic, ...]:
    if value is True:
        names: Sequence[object] = STATISTICS
    elif value is False:
        return ()
    elif isinstance(value, str):
        names = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        names = value
    else:
        expected = "a boolean, a statistic name, or a sequence of statistic names"
        if allow_mapping:
            expected += ", or a mapping from structure declarations to those selections"
        raise TypeError(f"stats must be {expected}")
    if not names:
        raise ValueError("stats cannot be empty; use stats=False to store none")
    statistics = tuple(_statistic(name) for name in names)
    seen: set[str] = set()
    for statistic in statistics:
        if statistic.name in seen:
            raise ValueError(f"duplicate Rumi statistic {statistic.name!r}")
        seen.add(statistic.name)
    return statistics


def _is_normalized_file_statistics(value: object) -> TypeGuard[_NormalizedFileStatistics]:
    return (
        isinstance(value, tuple)
        and bool(value)
        and all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], tuple)
            and all(isinstance(name, str) for name in item[1])
            for item in value
        )
    )


def _file_statistics(
    value: Mapping[str, _StatisticSelection] | _NormalizedFileStatistics,
) -> tuple[tuple[Leaf, tuple[_Statistic, ...]], ...]:
    if not value:
        raise ValueError("stats mapping cannot be empty; use stats=False to store none")
    result = []
    items = value.items() if isinstance(value, Mapping) else value
    for declaration, selected in items:
        if not isinstance(declaration, str):
            raise TypeError(f"Rumi stats keys must be structure declarations, got {type(declaration).__name__}")
        if selected is False:
            raise ValueError(f"Rumi stats for {declaration!r} cannot be False; omit it from the mapping instead")
        if isinstance(selected, Sequence) and not isinstance(selected, str | bytes) and not selected:
            raise ValueError(f"Rumi stats for {declaration!r} cannot be empty; omit it from the mapping instead")
        result.append((parse_leaf(declaration), _statistics(selected)))
    return tuple(result)


def _subset(array: np.ndarray, statistic: _Statistic, source: Path) -> np.ndarray:
    """The values one statistic describes: the whole array, a band, a time step or both."""
    cube = array.ndim == 4
    if statistic.time is not None and not cube:
        raise SampleError(f"Rumi statistic {statistic.name!r} needs a Cube, but {source.name!r} is an Image")
    bands = array.shape[1] if cube else array.shape[0]
    if statistic.band is not None and statistic.band >= bands:
        raise SampleError(
            f"Rumi statistic {statistic.name!r} reads band {statistic.band}, but {source.name!r} has {bands} bands"
        )
    if statistic.time is not None and statistic.time >= array.shape[0]:
        raise SampleError(
            f"Rumi statistic {statistic.name!r} reads time step {statistic.time}, "
            f"but {source.name!r} has {array.shape[0]} time steps"
        )
    band = slice(None) if statistic.band is None else statistic.band
    if not cube:
        return array[band]
    return array[slice(None) if statistic.time is None else statistic.time, band]


def _compute(array: np.ndarray, statistics: tuple[_Statistic, ...], source: Path) -> dict[str, float | None]:
    if array.ndim not in (3, 4):
        raise SampleError(f"Rumi arrays must have shape (B,Y,X) or (T,B,Y,X), got {array.shape} in {source.name!r}")
    if np.iscomplexobj(array):
        raise SampleError(f"Rumi statistics need real values, got {array.dtype} in {source.name!r}")
    # Statistics of the same band and time step share one pass over its valid values.
    subsets: dict[tuple[int | None, int | None], list[_Statistic]] = {}
    for statistic in statistics:
        subsets.setdefault((statistic.time, statistic.band), []).append(statistic)
    result: dict[str, float | None] = {}
    for group in subsets.values():
        values = _valid(_subset(array, group[0], source))
        integer = values.dtype.kind in "iu"
        wanted = [_PERCENTILES[statistic.kind] for statistic in group if statistic.kind in _PERCENTILES]
        percentiles = _integer_percentiles(values, wanted) if integer and wanted and values.size else {}
        for statistic in group:
            if not values.size:
                result[statistic.name] = None
            elif statistic.kind not in _PERCENTILES:
                result[statistic.name] = float(_REDUCERS[statistic.kind](values))
            elif integer:
                result[statistic.name] = percentiles[_PERCENTILES[statistic.kind]]
            else:
                result[statistic.name] = float(np.percentile(values, _PERCENTILES[statistic.kind]))
    return result


def _valid(values: np.ndarray) -> np.ndarray:
    if values.dtype == np.bool_:
        # Percentiles interpolate, which booleans cannot do.
        return values.ravel().view(np.uint8)
    if values.dtype.kind in "iu":
        return values.ravel()
    finite: np.ndarray = values[np.isfinite(values)]
    return finite


def _integer_percentiles(values: np.ndarray, percentiles: list[int]) -> dict[int, float]:
    """np.percentile, interpolated with Python ints because numpy's subtraction can wrap."""
    n = values.size
    positions = {}
    for q in percentiles:
        virtual = (n - 1) * (q / 100)
        previous = min(math.floor(virtual), n - 1)
        positions[q] = (previous, min(previous + 1, n - 1), virtual - previous)
    ranked = _ranked(
        values, sorted({rank for previous, following, _ in positions.values() for rank in (previous, following)})
    )
    result = {}
    for q, (previous, following, gamma) in positions.items():
        a, b = ranked[previous], ranked[following]
        difference = b - a
        result[q] = float(b) - difference * (1 - gamma) if gamma >= 0.5 else float(a) + difference * gamma
    return result


def _ranked(values: np.ndarray, ranks: list[int]) -> dict[int, int]:
    low, high = int(values.min()), int(values.max())
    if high - low >= _COUNTED_SPAN:
        partitioned = np.partition(values, ranks)
        return {rank: int(partitioned[rank]) for rank in ranks}
    # Signed subtraction may wrap, but the unsigned view is still the offset.
    offsets = (values - values.dtype.type(low)).view(f"u{values.dtype.itemsize}")
    cumulative = np.cumsum(np.bincount(offsets.astype(np.intp, copy=False)))
    return {rank: low + int(np.searchsorted(cumulative, rank, side="right")) for rank in ranks}


def _source(value: Path | None) -> Path:
    if value is None:
        raise ValueError("the Rumi extension requires one local asset for every metadata row")
    if value.suffix.lower() != ".rumi":
        raise ValueError(f"the Rumi extension only accepts .rumi assets, got {value.name!r}")
    return value


@dataclass(frozen=True)
class Rumi(Extension):
    """Inspect local ``.rumi`` assets during ``writer.run()``.

    ``stats=True`` stores the minimum, maximum, mean, population standard
    deviation and the 2nd and 98th percentiles of every valid value. A list of
    names such as ``["mean", "mean_b10", "p98_t0_b3"]`` stores exactly those,
    each restricted to a band, a time step, or both. A mapping from complete
    structure declarations to selections chooses statistics per file.
    """

    header: bool = True
    stats: bool | str | Sequence[str] | Mapping[str, _StatisticSelection] | _NormalizedFileStatistics = False
    _statistics: tuple[_Statistic, ...] = field(init=False, repr=False, compare=False)
    _file_statistics: tuple[tuple[Leaf, tuple[_Statistic, ...]], ...] = field(init=False, repr=False, compare=False)

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_namespace__: ClassVar[str | None] = RUMI_NAMESPACE
    __taco_row_local__: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if not isinstance(self.header, bool):
            raise TypeError("header must be a boolean")
        if isinstance(self.stats, Mapping) or _is_normalized_file_statistics(self.stats):
            by_file = _file_statistics(self.stats)
            unique: dict[str, _Statistic] = {}
            for _, selected in by_file:
                for statistic in selected:
                    unique.setdefault(statistic.name, statistic)
            statistics = tuple(unique.values())
            normalized: object = tuple(
                (leaf.declaration, tuple(statistic.name for statistic in selected)) for leaf, selected in by_file
            )
        else:
            by_file = ()
            statistics = _statistics(self.stats, allow_mapping=True)
            normalized = tuple(statistic.name for statistic in statistics)
        # Normalize mutable inputs so the frozen extension remains hashable.
        object.__setattr__(self, "stats", normalized)
        object.__setattr__(self, "_statistics", statistics)
        object.__setattr__(self, "_file_statistics", by_file)
        if not (self.header or statistics):
            raise ValueError("the Rumi extension requires header=True or at least one statistic")

    @property
    def requires(self) -> tuple[str, ...]:
        return ()

    @property
    def fields(self) -> pa.Schema:
        fields = []
        if self.header:
            fields.append(
                pa.field(
                    "header",
                    pa.binary(),
                    nullable=False,
                    metadata={b"description": b"Canonical external Rumi header"},
                )
            )
        for statistic in self._statistics:
            # Null when no selected value is finite or the statistic does not apply to this file.
            metadata = {b"description": statistic.description.encode()}
            if self._file_statistics:
                files = [
                    leaf.declaration
                    for leaf, selected in self._file_statistics
                    if statistic.name in {item.name for item in selected}
                ]
                metadata[b"taco:files"] = json.dumps(files).encode()
            fields.append(
                pa.field(
                    statistic.name,
                    pa.float64(),
                    nullable=True,
                    metadata=metadata,
                )
            )
        return pa.schema(fields)

    def configuration(self) -> Mapping[str, Any]:
        if self._file_statistics:
            stats: object = {
                leaf.declaration: [statistic.name for statistic in selected] for leaf, selected in self._file_statistics
            }
        else:
            stats = [statistic.name for statistic in self._statistics]
        return {
            "header": self.header,
            "stats": stats,
        }

    def _selected_for(self, path: str) -> tuple[_Statistic, ...]:
        if not self._file_statistics:
            return self._statistics
        contract_path = path.partition("/")[2]
        folder, _, name = contract_path.rpartition("/")
        parts = tuple(folder.split("/")) if folder else ()
        for leaf, selected in self._file_statistics:
            if leaf.folder == parts and leaf.match_index(name) is not None:
                return selected
        return ()

    def run(self, context: ExtensionContext) -> Mapping[str, Sequence[Any]]:
        # Validate the row-to-asset contract before importing the optional
        # runtime so malformed inputs report their own error deterministically.
        sources = tuple(_source(value) for value in context.assets)
        try:
            import rumi
        except ImportError as exc:
            raise ImportError("the Rumi extension requires 'taco-eo[rumi]'") from exc

        headers = []
        statistics: dict[str, list[float | None]] = {statistic.name: [] for statistic in self._statistics}
        actual_paths = context.columns["internal:relative_path"] if self._file_statistics else ("",) * len(sources)
        for source, path in zip(sources, actual_paths, strict=True):
            metadata = rumi.info(source=source)
            headers.append(metadata.header)
            selected = self._selected_for(str(path))
            computed: dict[str, float | None] = {}
            if selected:
                array = np.asarray(rumi.read(source, metadata.header))
                computed = _compute(array, selected, source)
            for name in statistics:
                statistics[name].append(computed.get(name))
        result: dict[str, Sequence[Any]] = {}
        if self.header:
            result["header"] = headers
        result.update(statistics)
        return result


__all__ = ["Rumi"]
