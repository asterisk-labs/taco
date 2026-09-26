from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SOURCE_FIXTURE = Path(__file__).resolve().parents[2] / "core" / "tests" / "data" / "taco_flat.zip"
FIXTURE = SOURCE_FIXTURE if SOURCE_FIXTURE.exists() else Path(__file__).parent / "data" / "taco_flat.zip"

# Simulate an install without writer dependencies.
SCRIPT = textwrap.dedent(
    """
    import importlib.abc
    import sys

    WRITER = {"cozip", "numpy", "pydantic", "pydantic_core", "pyproj", "shapely"}


    class Missing(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.partition(".")[0] in WRITER:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None


    sys.meta_path.insert(0, Missing())

    import taco

    dataset = taco.open_dataset(sys.argv[1])
    assert dataset.read().num_rows == 4
    assert dataset.sql("SELECT count(*) AS n FROM children").column("n").to_pylist()[0] > 0
    assert dataset.contract.levels == ("sample", "children")
    assert dataset._repr_html_()
    namespace = {}
    exec("from taco import *", namespace)
    assert namespace["read"] is taco.read
    assert "open_writer" not in namespace
    assert "__name__" in dir(taco)
    for name in ("open_writer", "validate", "export", "consolidate", "metadata", "extensions"):
        try:
            getattr(taco, name)
        except ImportError as exc:
            assert "pip install 'taco-eo[writer]'" in str(exc), exc
        else:
            raise AssertionError(f"taco.{name} loaded without the writer dependencies")
    try:
        from taco.container import INDEX_NAME
    except ImportError as exc:
        assert "pip install 'taco-eo[writer]'" in str(exc), exc
    else:
        raise AssertionError(f"taco.container.INDEX_NAME loaded without the writer dependencies: {INDEX_NAME}")
    print("read-only ok")
    """
)


def test_reading_needs_no_writer_dependencies() -> None:
    result = subprocess.run([sys.executable, "-c", SCRIPT, str(FIXTURE)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "read-only ok" in result.stdout


def test_writer_names_load_on_first_use() -> None:
    import taco
    from taco.container import INDEX_NAME, cozip_plan, cozip_write

    assert callable(taco.open_writer)
    assert taco.metadata.sample.STAC is taco.metadata.spatiotemporal.STAC
    assert "open_writer" in dir(taco)
    assert "open_writer" in taco.__all__
    assert INDEX_NAME == "__cozip__"
    assert callable(cozip_plan)
    assert callable(cozip_write)
