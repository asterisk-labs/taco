from typing import Annotated, Literal

from pydantic import Field

from ..container.parquet import Encoding
from ._base import SampleModel

Partition = Literal["train", "test", "validation", "excluded"]


class Split(SampleModel):
    split: Annotated[Partition, Encoding("dictionary")] = Field(
        description="Dataset split. `excluded` is held out of every partition"
    )

    # The fields below are optional. They say where `split` came from, and they
    # live in this model so that all of a sample's split columns share one
    # namespace (`ml:split`, `ml:split_original`, ...).
    split_original: str | None = Field(
        default=None,
        description="The publisher's own name for this sample's split, such as `valid` "
                    "or `val`; empty when the publisher gave none",
    )
    split_rule: str | None = Field(
        default=None,
        description="The rule that assigned `split`, as `<namespace>/<rule>-v<n>`. Only "
                    "needed when the rule differs between samples; a rule shared by "
                    "every sample belongs in the collection's split strategy",
    )
    split_noleak: Partition | None = Field(
        default=None,
        description="A second split that stays disjoint across collections built on the "
                    "same imagery, so training on one never tests on another's images",
    )
    split_noleak_rule: str | None = Field(
        default=None,
        description="The rule that assigned `split_noleak`, when it differs between samples",
    )


__all__ = ["Partition", "Split"]
