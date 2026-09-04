from __future__ import annotations

from cozip._taco import plan as cozip_plan
from cozip._taco import write as cozip_write

__all__ = ["INDEX_NAME", "cozip_plan", "cozip_write"]

# Reserved by cozip specification 5.3; a TACO archive must not use it.
INDEX_NAME = "__cozip__"
