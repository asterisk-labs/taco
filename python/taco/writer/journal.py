from __future__ import annotations

import pickle
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class Journal:
    """Pickle stream on disk; keeps writer memory flat for huge datasets."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = path.open("ab")
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def append(self, item: Any) -> None:
        pickle.dump(item, self._file, protocol=pickle.HIGHEST_PROTOCOL)
        self._count += 1

    def flush(self) -> None:
        if not self._file.closed:
            self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            self._file.close()

    def __iter__(self) -> Iterator[Any]:
        self.flush()
        with self.path.open("rb") as stream:
            while True:
                try:
                    yield pickle.load(stream)
                except EOFError:
                    return
