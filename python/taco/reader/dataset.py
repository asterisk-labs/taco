from __future__ import annotations

from collections.abc import Sequence

import pyarrow as pa

from ..contract.contract import CHILDREN_LEVEL, Contract
from ..contract.naming import SAMPLE_INDEX, rumi_file_field
from ..contract.structure import Leaf
from ..errors import ContainerError
from . import engine, native
from .collection import merge_collections
from .query import normalize_files
from .source import Source, normalize_sources


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _output_name(declaration: str, *, variable: bool) -> str:
    # Generated columns keep the structure path, or the prefix of a variable sequence.
    return declaration.partition("*")[0] if variable else declaration


def _wide_columns(contract: Contract, leaf: Leaf) -> list[tuple[str, str]]:
    # Metadata fields contain exactly one ':', so the double separator cannot
    # collide with user metadata. Rumi fields follow the location so consumers
    # can pair the header and statistics with the asset they describe.
    name = _output_name(leaf.declaration, variable=leaf.variable)
    columns = [("taco:location", f"{name}::location")]
    level = "/".join((CHILDREN_LEVEL, *leaf.folder))
    for field, spec in contract.metadata.get(level, {}).items():
        suffix = rumi_file_field(field)
        if suffix is not None and (spec.files is None or leaf.declaration in spec.files):
            columns.append((field, f"{name}::{suffix}"))
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

    def level(self, name: str) -> pa.Table:
        """Return the rows of one metadata level, such as ``"sample"`` or ``"children"``.

        The table has that level's own columns and nothing from other levels, so
        it works even when a field name is reused with a different type at
        another level (a list here, a single value there).

        Rows come in the order they are stored. When the dataset is split into
        several archives, rows are grouped by archive and a ``source_file`` column
        says which archive each row came from; this is needed because row ids
        (``internal:current_id``) start again from 0 in every archive.
        """
        if name not in self.contract.levels:
            raise ContainerError(f"taco: no metadata level {name!r}; have {list(self.contract.levels)}")
        opened = [native.NativeDataset(source) for source in self.sources]
        query = native.sql(opened, idx=None, level=name, pivoted=False, files=None, location=False)
        order = ("source_file, " if len(self.sources) > 1 else "") + '"internal:current_id"'
        if self.collection.sources is not None:
            # A TACOCAT keeps each partition's own ids, so rows are ordered by the
            # partition's position in `taco:sources`, then by id within it.
            files = [entry["file"] for entry in self.collection.sources.get("partitions", ())]
            rank = " ".join(f"WHEN {_literal(file)} THEN {position}" for position, file in enumerate(files))
            order = f'CASE "internal:source_file" {rank} END, "internal:current_id"'
        return engine.open_reader().execute(f"SELECT * FROM ({query}) ORDER BY {order}").to_arrow_table()

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
