from typing import Annotated, Literal

from pydantic import Field

from ..container.parquet import Encoding
from ._base import SampleModel


class Split(SampleModel):
    split: Annotated[Literal["train", "test", "validation"], Encoding("dictionary")] = Field(
        description="Dataset split"
    )


__all__ = ["Split"]
