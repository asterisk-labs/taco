from __future__ import annotations

from pathlib import Path

import pytest

from taco import _publish


def test_publish_many_rejects_duplicate_targets(tmp_path: Path) -> None:
    target = tmp_path / "target"
    with pytest.raises(ValueError, match="more than once"):
        _publish.publish_many([(tmp_path / "a", target), (tmp_path / "b", target)], overwrite=True)
