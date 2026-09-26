import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cozip import INDEX_NAME, cozip_plan, cozip_write

__all__ = ["INDEX_NAME", "cozip_plan", "cozip_write"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module 'taco.container' has no attribute {name!r}")
    try:
        module = importlib.import_module(".cozip", __name__)
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "cozip":
            raise
        raise ImportError(
            f"taco.container.{name} needs the writer: pip install 'taco-eo[writer]' (missing {exc.name})"
        ) from exc
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
