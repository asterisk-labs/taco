from typing import Literal

from pydantic import Field

from ._base import SampleModel


class Split(SampleModel):
    split: Literal["train", "test", "validation"] = Field(description="Dataset split")


__all__ = ["Split"]
