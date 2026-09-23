from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path


class IdentifierIndex:
    """Disk-backed uniqueness index for logical sample identifiers."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=OFF")
        self._connection.execute("PRAGMA synchronous=OFF")
        self._connection.execute("CREATE TABLE ids (value TEXT PRIMARY KEY) WITHOUT ROWID")

    def contains(self, value: str) -> bool:
        row = self._connection.execute("SELECT 1 FROM ids WHERE value = ?", (value,)).fetchone()
        return row is not None

    def add(self, value: str) -> bool:
        cursor = self._connection.execute("INSERT OR IGNORE INTO ids VALUES (?)", (value,))
        return cursor.rowcount == 1

    def duplicates(self, values: Iterable[str], *, limit: int = 3) -> list[str]:
        duplicates: list[str] = []
        for value in values:
            if self.contains(value):
                duplicates.append(value)
                if len(duplicates) == limit:
                    break
        return duplicates

    def close(self) -> None:
        self._connection.close()


__all__ = ["IdentifierIndex"]
