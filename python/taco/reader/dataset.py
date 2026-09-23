from __future__ import annotations

import re
from collections.abc import Sequence

import pyarrow as pa

from ..contract.contract import CHILDREN_LEVEL, Contract
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


# Rumi assets are read statelessly with their header, so a wide read carries it
# next to the location of every leaf whose level declares it.
_HEADER_FIELD = "rumi:header"


def _output_name(declaration: str, *, variable: bool) -> str:
    name = declaration.partition("*")[0] if variable else declaration
    return name.replace("/", "__")


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
        self.collection = merge_collections(self.sources)

    @property
    def contract(self) -> Contract:
        return self.collection.contract

    def read(self, *, files: str | Sequence[str] | None = None) -> pa.Table:
        """Read all samples, optionally selecting structural file columns."""
        return self.sql(self._read_query(normalize_files(files)))

    def sql(self, query: str) -> pa.Table:
        """Query the dataset's ``data``, ``files``, and metadata relations."""
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
        # sample_id is local to a partition, so source_file completes the key
        # whenever several archives share one logical dataset.
        keys = ["sample_id"]
        if len(self.sources) > 1 or self.collection.sources is not None:
            keys.append("source_file")
        using = ", ".join(_identifier(name) for name in keys)
        order_keys = ["source_file", "sample_id"] if "source_file" in keys else keys
        order = ", ".join(f"d.{_identifier(name)}" for name in order_keys)

        selected = set(self.contract.structure if files is None else files)
        unknown = sorted(selected.difference(self.contract.structure))
        if unknown:
            raise ContainerError(f"taco: files contains unknown structure leaf: {', '.join(unknown)}")
        leaves = [leaf for leaf in self.contract.leaves if leaf.declaration in selected]
        if not leaves:
            raise ContainerError("taco: no structure leaf matches the requested files")

        # Pivot only the requested file locations, then join them to the
        # metadata-only data relation. This avoids grouping user metadata and
        # keeps samples whose optional files are missing.
        aggregates: list[str] = []
        file_columns: list[str] = []
        filters: list[str] = []
        for leaf in leaves:
            if leaf.variable:
                assert leaf.prefix is not None
                path_prefix = "/".join((*leaf.folder, leaf.prefix))
                pattern = "^" + re.escape(path_prefix) + r"(0|[1-9][0-9]*)" + re.escape(leaf.suffix) + "$"
                match = f"regexp_matches(f.path, {_literal(pattern)})"
                filters.append(f"regexp_matches(path, {_literal(pattern)})")
                for source, name in _wide_columns(self.contract, leaf):
                    output = _identifier(name)
                    empty = "[]::BLOB[]" if source == _HEADER_FIELD else "[]::VARCHAR[]"
                    aggregates.append(
                        f"list(f.{_identifier(source)} ORDER BY "
                        f"TRY_CAST(regexp_extract(f.path, {_literal(pattern)}, 1) AS BIGINT)) "
                        f"FILTER (WHERE {match}) AS {output}"
                    )
                    file_columns.append(f"COALESCE(v.{output}, {empty}) AS {output}")
            else:
                path = _literal(leaf.declaration)
                filters.append(f"path = {path}")
                for source, name in _wide_columns(self.contract, leaf):
                    output = _identifier(name)
                    aggregates.append(f"MAX(f.{_identifier(source)}) FILTER (WHERE f.path = {path}) AS {output}")
                    file_columns.append(f"v.{output} AS {output}")

        selected_files = " OR ".join(filters)
        group = ", ".join(f"f.{_identifier(name)}" for name in keys)
        return (
            f"WITH file_values AS (SELECT {group}, {', '.join(aggregates)} "
            f"FROM (SELECT * FROM files WHERE {selected_files}) AS f GROUP BY {group}) "
            f"SELECT d.*, {', '.join(file_columns)} FROM data AS d "
            f"LEFT JOIN file_values AS v USING ({using}) ORDER BY {order}"
        )

    def _execute_sql(self, query: str) -> pa.Table:
        # The native core supplies trusted Parquet scans. User SQL runs only
        # against these logical relations, never as part of a file expression.
        opened = [native.NativeDataset(source) for source in self.sources]
        wide_without_locations = native.sql(opened, idx=None, level=None, pivoted=True, files=None, location=False)
        flat = native.sql(opened, idx=None, level=None, pivoted=False, files=None, location=True)

        file_columns = [name for leaf in self.contract.leaves for _, name in _wide_columns(self.contract, leaf)]
        data = wide_without_locations
        if file_columns:
            excluded = ", ".join(_identifier(name) for name in file_columns)
            data = f"SELECT * EXCLUDE ({excluded}) FROM ({data}) AS taco_data"

        relations = [("data", data), ("files", flat)]
        # Slashes are not convenient relation names in SQL; nested metadata
        # levels use the same double-underscore convention as wide file names.
        relations.extend(
            (
                level.replace("/", "__"),
                native.sql(opened, idx=None, level=level, pivoted=True, files=None, location=False),
            )
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
