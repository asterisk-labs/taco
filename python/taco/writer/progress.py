from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from types import TracebackType
from typing import Protocol, cast


class _Tqdm(Protocol):
    def update(self, value: int = 1) -> object: ...

    def close(self) -> None: ...


class Progress:
    def __init__(self, enabled: bool, total: int, description: str, unit: str = "sample") -> None:
        self._bar: _Tqdm | None = None
        if not enabled:
            return
        try:
            module = import_module("tqdm.auto")
        except ModuleNotFoundError as error:
            if error.name != "tqdm":
                raise
            raise ImportError("progress=True requires tqdm; install taco-eo[progress]") from None
        factory = cast(Callable[..., _Tqdm], module.tqdm)
        self._bar = factory(total=total, desc=description, unit=unit, leave=False, dynamic_ncols=True)

    def __enter__(self) -> Progress:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._bar is not None:
            self._bar.close()

    def update(self, value: int = 1) -> None:
        if self._bar is not None:
            self._bar.update(value)


__all__ = ["Progress"]
