from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa
import pytest
from pydantic import BaseModel

import taco
from taco.container.view import open_view
from taco.errors import CollectionError, ContractError, SampleError, WriterError


class Value(BaseModel):
    value: int


@dataclass(frozen=True)
class AssetValue(taco.Extension):
    @property
    def input_model(self) -> type[BaseModel]:
        return Value

    @property
    def requires(self) -> tuple[str, ...]:
        return ("value:value",)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("doubled", pa.int64(), nullable=False), pa.field("asset_name", pa.string())])

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return {
            "doubled": [item * 2 for item in context.columns["value:value"]],
            "asset_name": [asset.name if asset is not None else None for asset in context.assets],
        }


@dataclass(frozen=True)
class Generated(taco.Extension):
    dependency: str

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    @property
    def requires(self) -> tuple[str, ...]:
        return (self.dependency,)

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.int64(), nullable=False)])

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return {"value": context.columns[self.dependency]}


COMPLETE_BATCHES: list[int] = []


@dataclass(frozen=True)
class CompleteGenerated(taco.Extension):
    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})
    __taco_complete_level__: ClassVar[bool] = True

    @property
    def requires(self) -> tuple[str, ...]:
        return ()

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.int64(), nullable=False)])

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[Any]]:
        COMPLETE_BATCHES.append(len(context))
        return {"value": list(range(len(context)))}


@dataclass(frozen=True)
class Broken(taco.Extension):
    behavior: str

    __taco_scopes__: ClassVar[frozenset[str]] = frozenset({"sample"})

    @property
    def requires(self) -> tuple[str, ...]:
        return ()

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.int64(), nullable=False)])

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[Any]]:
        if self.behavior == "mapping":
            return []  # type: ignore[return-value]
        if self.behavior == "keys":
            return {"other": [1] * len(context)}
        if self.behavior == "length":
            return {"value": []}
        return {"value": ["not-an-integer"] * len(context)}


@dataclass(frozen=True)
class CollectionTagged(taco.Extension):
    value: str

    @property
    def requires(self) -> tuple[str, ...]:
        return ()

    @property
    def fields(self) -> pa.Schema:
        return pa.schema([pa.field("value", pa.string(), nullable=False)])

    def collection_metadata(self) -> Mapping[str, Any]:
        return {"setting": self.value}

    def run(self, context: taco.ExtensionContext) -> Mapping[str, Sequence[Any]]:
        return {"value": [self.value] * len(context)}


def collection(contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id="extension-contract",
        description="Extension contract tests",
        licenses=["MIT"],
        providers=["TACO tests"],
        tasks=["other"],
    )


def test_extension_context_requires_aligned_rows() -> None:
    with pytest.raises(ValueError, match="same length"):
        taco.ExtensionContext("sample", {"x:value": [1, 2]}, (None,))


def test_extension_combines_inputs_with_local_assets(tmp_path: Path) -> None:
    source = tmp_path / "value.bin"
    source.write_bytes(b"value")
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", value=AssetValue())],
    )
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.add(
            taco.Sample(
                id="u7",
                assets=taco.Asset(source, path="data.bin"),
                metadata=taco.Metadata(value=Value(value=4)),
            )
        )
        writer.run()
    row = open_view(tmp_path / "dataset").level("sample").to_pylist()[0]
    assert row["value:value"] == 4
    assert row["value:doubled"] == 8
    assert row["value:asset_name"] == "value.bin"


def test_sample_extension_asset_is_defined_by_the_contract(tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["image*[1,2].bin"],
        metadata=[taco.Level("sample", value=AssetValue())],
    )
    samples = [
        taco.Sample(
            id="one",
            assets=[taco.Asset(b"one", path="image0.bin")],
            metadata=taco.Metadata(value=Value(value=1)),
        ),
        taco.Sample(
            id="two",
            assets=[taco.Asset(b"one", path="image0.bin"), taco.Asset(b"two", path="image1.bin")],
            metadata=taco.Metadata(value=Value(value=2)),
        ),
    ]
    with taco.open_writer(collection(contract), tmp_path / "dataset") as writer:
        writer.extend(samples)
        writer.run()

    table = open_view(tmp_path / "dataset").level("sample")
    assert table.column("value:asset_name").to_pylist() == [None, None]


def test_complete_level_extension_receives_all_rows(tmp_path: Path) -> None:
    COMPLETE_BATCHES.clear()
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", complete=CompleteGenerated())],
    )
    with taco.open_writer(collection(contract), tmp_path / "complete", batch_size=1) as writer:
        writer.extend(taco.Sample(id=f"u8-{index}", assets=b"x") for index in range(3))
        writer.run()

    # The second call is the writer's one-row independence check.
    assert COMPLETE_BATCHES == [3, 1]
    values = open_view(tmp_path / "complete").level("sample").column("complete:value").to_pylist()
    assert values == [0, 1, 2]


def test_executable_extensions_reject_a_dependency_cycle() -> None:
    with pytest.raises(ContractError, match="cycle"):
        taco.Contract(
            structure=["data.bin"],
            metadata=[taco.Level("sample", first=Generated("second:value"), second=Generated("first:value"))],
        )


@pytest.mark.parametrize(
    ("behavior", "message"),
    [
        ("mapping", "return a mapping"),
        ("keys", "returned"),
        ("length", "wrong number of rows"),
        ("type", "invalid extension output"),
    ],
)
def test_writer_rejects_invalid_extension_outputs(behavior: str, message: str, tmp_path: Path) -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", broken=Broken(behavior))],
    )
    with taco.open_writer(collection(contract), tmp_path / behavior) as writer:
        writer.add(taco.Sample(id="u9", assets=b"x"))
        with pytest.raises(SampleError, match=message):
            writer.run()
    assert not (tmp_path / behavior).exists()


def test_extensions_reject_conflicting_collection_metadata() -> None:
    contract = taco.Contract(
        structure=["folder/data.bin"],
        metadata=[
            taco.Level("sample", tag=CollectionTagged("first")),
            taco.Level("children", tag=CollectionTagged("second")),
        ],
    )
    with pytest.raises(ContractError, match="conflicting collection metadata"):
        contract.extension_metadata()


def test_collection_rejects_metadata_that_conflicts_with_an_extension() -> None:
    contract = taco.Contract(
        structure=["data.bin"],
        metadata=[taco.Level("sample", tag=CollectionTagged("active"))],
    )
    with pytest.raises(CollectionError, match="conflicts with the active extension"):
        collection(contract).replace(metadata=taco.CollectionMetadata.from_flat({"tag:setting": "different"}))


def test_append_rejects_changed_extension_metadata(tmp_path: Path) -> None:
    def tagged(value: str) -> taco.Collection:
        contract = taco.Contract(
            structure=["data.bin"],
            metadata=[taco.Level("sample", tag=CollectionTagged(value))],
        )
        return collection(contract)

    output = tmp_path / "dataset"
    with taco.open_writer(tagged("first"), output) as writer:
        writer.add(taco.Sample(id="first", assets=b"x"))
        writer.run()

    with taco.open_writer(tagged("second"), output, append=True) as writer:
        writer.add(taco.Sample(id="second", assets=b"y"))
        with pytest.raises(WriterError, match="different extension metadata"):
            writer.run()
