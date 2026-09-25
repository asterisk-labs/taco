from __future__ import annotations

from collections.abc import Sequence

import pyarrow as pa

from ..contract.contract import CHILDREN_LEVEL, Contract
from ..contract.naming import SAMPLE_INDEX
from ..contract.structure import Leaf
from ..errors import ContainerError
from . import engine, native
from .collection import merge_collections
from .query import normalize_files
from .source import Source, normalize_sources


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


# Rumi assets are read statelessly with their header, so a wide read carries it
# next to the location of every leaf whose level declares it.
_HEADER_FIELD = "rumi:header"


def _output_name(declaration: str, *, variable: bool) -> str:
    # Generated columns keep the structure path, or the prefix of a variable sequence.
    return declaration.partition("*")[0] if variable else declaration


def _has_header(contract: Contract, leaf: Leaf) -> bool:
    level = "/".join((CHILDREN_LEVEL, *leaf.folder))
    return _HEADER_FIELD in contract.metadata.get(level, {})


def _wide_columns(contract: Contract, leaf: Leaf) -> list[tuple[str, str]]:
    # Metadata fields contain exactly one ':', so the double separator cannot
    # collide with user metadata.
    name = _output_name(leaf.declaration, variable=leaf.variable)
    columns = [("taco:location", f"{name}::location")]
    if _has_header(contract, leaf):
        columns.append((_HEADER_FIELD, f"{name}::header"))
    return columns


class Dataset:
    def __init__(self, source: Source) -> None:
        self.sources = normalize_sources(source)
        self._opened = tuple(native.NativeDataset(source) for source in self.sources)
        self.collection = merge_collections(self.sources, self._opened)

    @property
    def contract(self) -> Contract:
        return self.collection.contract

    def read(self, *, files: str | Sequence[str] | None = None) -> pa.Table:
        """Read all samples, optionally selecting structural file columns."""
        return self.sql(self._read_query(normalize_files(files)))

    def sql(self, query: str) -> pa.Table:
        """Query the public ``dataset`` view or a raw metadata level."""
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        query = query.strip()
        if query.endswith(";"):
            query = query[:-1].rstrip()
        if not query:
            raise ValueError("query must not be empty")
        if "\0" in query:
            raise ValueError("query must not contain NUL")
        return self._execute_sql(query)

    def _read_query(self, files: list[str] | None) -> str:
        selected = set(self.contract.structure if files is None else files)
        unknown = sorted(selected.difference(self.contract.structure))
        if unknown:
            raise ContainerError(f"taco: files contains unknown structure leaf: {', '.join(unknown)}")
        leaves = [leaf for leaf in self.contract.leaves if leaf.declaration in selected]
        if not leaves:
            raise ContainerError("taco: no structure leaf matches the requested files")
        excluded = [
            name
            for leaf in self.contract.leaves
            if leaf.declaration not in selected
            for _, name in _wide_columns(self.contract, leaf)
        ]
        projection = "*"
        if excluded:
            projection += " EXCLUDE (" + ", ".join(_identifier(name) for name in excluded) + ")"
        return f"SELECT {projection} FROM dataset ORDER BY {_identifier(SAMPLE_INDEX)}"

    def _execute_sql(self, query: str) -> pa.Table:
        complete = native.sql(self._opened, idx=None, level=None, pivoted=True, files=None, location=True)
        relations = [("dataset", complete)]
        relations.extend(
            (level, native.sql(self._opened, idx=None, level=level, pivoted=True, files=None, location=False))
            for level in self.contract.levels
        )
        context = ",\n".join(f"{_identifier(name)} AS ({sql})" for name, sql in relations)
        statement = f"WITH {context}\nSELECT * FROM (\n{query}\n) AS taco_query"
        return engine.open_reader().execute(statement).to_arrow_table()

    def __repr__(self) -> str:
        location = f"source={self.sources[0]!r}" if len(self.sources) == 1 else f"sources={len(self.sources)}"
        return f"Dataset({self.collection.id!r}, {location})"

    def _repr_html_(self) -> str:
        from .._repr import dataset_html

        return dataset_html(self)


__all__ = ["Dataset"]
