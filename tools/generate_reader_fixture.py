"""Regenerate the ZIP shared by the JavaScript, Julia, and R reader tests."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel

import taco

ROOT = Path(__file__).resolve().parents[1]
R_FIXTURE = ROOT / "r/tests/testthat/data/taco.zip"
JULIA_FIXTURE = ROOT / "julia/test/data/taco.zip"


class ML(BaseModel):
    split: str


class File(BaseModel):
    role: str


def main() -> None:
    contract = taco.Contract(
        structure=["image.bin", "mask.bin"],
        metadata=[
            taco.Level("sample", ml=ML),
            taco.Level("children", file=File),
        ],
    )
    collection = taco.Collection(
        contract=contract,
        id="taco-fixture",
        description="Two-leaf fixture for the JavaScript, Julia, and R readers",
        licenses=["CC-BY-4.0"],
        providers=[{"name": "Asterisk Labs", "roles": ["producer"]}],
        tasks=["segmentation"],
        title="taco-fixture",
    )
    R_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with taco.open_writer(collection, R_FIXTURE, overwrite=True) as writer:
        for index, split in enumerate(("train", "train", "test")):
            writer.add(
                taco.Sample(
                    id=f"sample-{index}",
                    metadata=taco.Metadata(ml=ML(split=split)),
                    assets=[
                        taco.Asset(
                            bytes([index + 1]) * 64,
                            path="image.bin",
                            metadata=taco.Metadata(file=File(role="image")),
                        ),
                        taco.Asset(
                            bytes([index + 11]) * 32,
                            path="mask.bin",
                            metadata=taco.Metadata(file=File(role="mask")),
                        ),
                    ],
                )
            )
        writer.run()
    JULIA_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(R_FIXTURE, JULIA_FIXTURE)


if __name__ == "__main__":
    main()
