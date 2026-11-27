from __future__ import annotations

import json
import multiprocessing
import os
import re
import shutil
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel

import taco
from taco.errors import ContainerError


class Split(BaseModel):
    split: str
    n_images: Annotated[int, pa.int32()]


class Kind(BaseModel):
    kind: str


class Raster(BaseModel):
    resolution: Annotated[int, pa.int32()]


class LocationMetadata(BaseModel):
    location: str


def payload(tag: str, index: int) -> bytes:
    return f"{tag}-{index}:".encode() * 40


def collection(name: str, contract: taco.Contract) -> taco.Collection:
    return taco.Collection(
        contract=contract,
        id=name,
        description=name,
        licenses=["MIT"],
        providers=[{"name": "TACO tests"}],
        tasks=["other"],
    )


def write(name: str, contract: taco.Contract, samples: list[taco.Sample], output: Path, **options) -> Path:
    with taco.open_writer(collection(name, contract), output, **options) as writer:
        writer.extend(samples)
        return writer.run().path


def nested_samples() -> list[taco.Sample]:
    return [
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(info=Split(split="train" if index % 2 == 0 else "val", n_images=index)),
            folders=[
                taco.Folder("before", metadata=taco.Metadata(node=Kind(kind="imagery"))),
                taco.Folder("after", metadata=taco.Metadata(node=Kind(kind="imagery"))),
            ],
            assets=[
                taco.Asset(
                    payload("b02", index), path="before/B02.bin", metadata=taco.Metadata(raster=Raster(resolution=10))
                ),
                taco.Asset(
                    payload("b03", index), path="before/B03.bin", metadata=taco.Metadata(raster=Raster(resolution=10))
                ),
                taco.Asset(
                    payload("a02", index), path="after/B02.bin", metadata=taco.Metadata(raster=Raster(resolution=20))
                ),
                taco.Asset(
                    payload("change", index), path="change.bin", metadata=taco.Metadata(node=Kind(kind="label"))
                ),
            ],
        )
        for index in range(3)
    ]


NESTED = taco.Contract(
    structure=["before/B02.bin", "before/B03.bin", "after/B02.bin", "change.bin"],
    metadata=[
        taco.Level("sample", info=Split),
        taco.Level("children", node=Kind),
        taco.Level("children/before", raster=Raster),
        taco.Level("children/after", raster=Raster),
    ],
)


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("core-reader")
    write("nested", NESTED, nested_samples(), root / "nested.zip")
    write("nested", NESTED, nested_samples(), root / "nested")

    variable = taco.Contract(
        structure=["img*[1,3].bin", "mask.bin"],
        metadata=[taco.Level("sample", info=Split), taco.Level("children", node=Kind)],
    )
    samples = []
    for index in range(3):
        assets = [
            taco.Asset(
                payload(f"img{number}", index), path=f"img{number}.bin", metadata=taco.Metadata(node=Kind(kind="image"))
            )
            for number in range(index + 1)
        ]
        assets.append(
            taco.Asset(payload("mask", index), path="mask.bin", metadata=taco.Metadata(node=Kind(kind="label")))
        )
        samples.append(
            taco.Sample(
                id=f"s{index}",
                metadata=taco.Metadata(info=Split(split="train", n_images=index + 1)),
                assets=assets,
            )
        )
    write("variable", variable, samples, root / "variable.zip")

    nested_variable = taco.Contract(structure=["before/img*[1,3].bin"])
    write(
        "nested-variable",
        nested_variable,
        [
            taco.Sample(
                id="s0",
                assets=[taco.Asset(payload(f"img{number}", 0), path=f"before/img{number}.bin") for number in range(2)],
            )
        ],
        root / "nested-variable.zip",
    )

    shadow = taco.Contract(
        structure=["before/B02.bin", "change.bin"],
        metadata=[
            taco.Level("sample", info=Split),
            taco.Level("children", raster=Raster),
            taco.Level("children/before", raster=Raster),
        ],
    )
    samples = [
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(info=Split(split="train", n_images=1)),
            folders=[taco.Folder("before", metadata=taco.Metadata(raster=Raster(resolution=1)))],
            assets=[
                taco.Asset(
                    payload("b02", index), path="before/B02.bin", metadata=taco.Metadata(raster=Raster(resolution=3))
                ),
                taco.Asset(
                    payload("change", index), path="change.bin", metadata=taco.Metadata(raster=Raster(resolution=2))
                ),
            ],
        )
        for index in range(2)
    ]
    write("shadow", shadow, samples, root / "shadow.zip")

    single = taco.Contract(structure=["data.bin"], metadata=[taco.Level("sample", info=Split)])
    samples = [
        taco.Sample(
            id=f"s{index}",
            assets=payload("sample", index),
            metadata=taco.Metadata(info=Split(split="train", n_images=index)),
        )
        for index in range(6)
    ]
    write("single", single, samples, root / "single.zip")
    write("single", single, samples, root / "single")

    (root / "catalog").mkdir()
    write("nested", NESTED, nested_samples(), root / "catalog" / "part.zip", partition_by="info:split")
    return root


def by_sample(table: pa.Table) -> list[dict]:
    return sorted(
        table.to_pylist(),
        key=lambda row: (row.get("source_file") or "", row["taco:sample_index"], row.get("path") or ""),
    )


def read_location(location: str) -> bytes:
    """The bytes at a local location: a file, or a /vsisubfile/ range of a local ZIP."""
    match = re.fullmatch(r"/vsisubfile/(\d+)_(\d+),(.+)", location)
    if match is None:
        return Path(location).read_bytes()
    with Path(match[3]).open("rb") as stream:
        stream.seek(int(match[1]))
        return stream.read(int(match[2]))


def test_wide_rows_have_a_location_per_leaf(data: Path) -> None:
    table = taco.read(data / "nested.zip")
    assert table.num_rows == 3
    assert table.column("taco:sample_index").to_pylist() == [0, 1, 2]
    row = by_sample(table)[0]
    assert row["before/B02.bin::location"].startswith("/vsisubfile/")
    assert row["before/B02.bin::location"].endswith(str((data / "nested.zip").resolve()))
    assert "change.bin::location" in taco.open_dataset(data / "nested.zip").sql("SELECT * FROM dataset").column_names


def test_generated_columns_do_not_collide_with_metadata(tmp_path: Path) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    contract = taco.Contract(
        structure=["image"],
        metadata=[taco.Level("sample", image=LocationMetadata)],
    )
    output = write(
        "column-collision",
        contract,
        [
            taco.Sample(
                id="s2",
                assets=taco.Asset(b"payload", path="image"),
                metadata=taco.Metadata(image=LocationMetadata(location="metadata-value")),
            )
        ],
        tmp_path / "collision.zip",
    )

    for table in (taco.read(output), engine.open_reader().execute(native_sql(output)).to_arrow_table()):
        assert table.column_names.count("image:location") == 1
        assert table.column("image:location").to_pylist() == ["metadata-value"]
        assert table.column("image::location")[0].as_py().startswith("/vsisubfile/")


def test_reader_ignores_a_stored_public_sample_index(tmp_path: Path) -> None:
    output = write(
        "reserved-column",
        taco.Contract(structure=["image.bin"]),
        [taco.Sample(id="s0", assets=b"payload")],
        tmp_path / "reserved-column",
    )
    sample_path = output / "METADATA" / "sample.parquet"
    table = pq.read_table(sample_path).append_column(
        pa.field("taco:sample_index", pa.uint64(), nullable=False),
        pa.array([99], pa.uint64()),
    )
    pq.write_table(table, sample_path)

    dataset = taco.open_dataset(output)
    result = dataset.read()
    assert result.column_names.count("taco:sample_index") == 1
    assert result.column("taco:sample_index").to_pylist() == [0]
    assert "taco:sample_index" not in dataset.sql("SELECT * FROM sample").column_names
    assert not taco.validate(output).ok


def test_dataset_and_raw_sql_relations(data: Path) -> None:
    path = data / "nested.zip"
    dataset = taco.open_dataset(path)
    assert taco.read(path, files=["change.bin"]).column_names[-1] == "change.bin::location"
    assert "before/B02.bin::location" not in taco.read(path, files=["change.bin"]).column_names
    assert "change.bin::location" in dataset.sql("SELECT * FROM dataset").column_names
    assert dataset.sql('SELECT "taco:sample_index" FROM dataset WHERE "taco:sample_index" = 1').column(
        "taco:sample_index"
    ).to_pylist() == [1]
    assert sorted(
        dataset.sql(
            'SELECT "taco:sample_index" FROM dataset WHERE "taco:sample_index" >= 1 AND "taco:sample_index" < 3'
        )
        .column("taco:sample_index")
        .to_pylist()
    ) == [1, 2]

    level = dataset.sql('SELECT * FROM "children/before"')
    assert level.num_rows == 6
    assert sorted(level.column("internal:relative_path").to_pylist())[0] == "0/before/B02.bin"


def test_variable_leaves_are_ordered_lists(data: Path) -> None:
    path = data / "variable.zip"
    rows = by_sample(taco.read(path))
    assert [(row["info:n_images"], len(row["img::location"])) for row in rows] == [(1, 1), (2, 2), (3, 3)]
    assert "img::location" in taco.open_dataset(path).sql("SELECT * FROM dataset").column_names

    nested = taco.read(data / "nested-variable.zip")
    assert nested.column_names == ["taco:sample_index", "id", "before/img::location"]
    assert len(nested.column("before/img::location")[0].as_py()) == 2


def test_a_sequence_names_only_the_indices_below_its_maximum(tmp_path: Path) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    def long_paths(source: Path, files: list[str]) -> list[str]:
        statement = native_sql(source, layout="long", files=files)
        return sorted(engine.open_reader().execute(statement).to_arrow_table().column("path").to_pylist())

    # img*[1,3].tif names img0.tif to img2.tif, so img10.tif is a fixed file of its own.
    contract = taco.Contract(structure=["img*[1,3].tif", "img10.tif"])
    names = ["img0.tif", "img1.tif", "img10.tif"]
    sample = taco.Sample(id="s0", assets=[taco.Asset(name.encode(), path=name) for name in names])
    for output in (tmp_path / "bounded.zip", tmp_path / "bounded"):
        write("bounded", contract, [sample], output)
        row = taco.read(output).to_pylist()[0]
        assert [read_location(location) for location in row["img::location"]] == [b"img0.tif", b"img1.tif"]
        assert read_location(row["img10.tif::location"]) == b"img10.tif"
        assert len(taco.read(output, files=["img*[1,3].tif"]).column("img::location")[0].as_py()) == 2
        assert long_paths(output, ["img*[1,3].tif"]) == ["img0.tif", "img1.tif"]
        assert long_paths(output, ["img10.tif"]) == ["img10.tif"]


def test_raw_relations_keep_redeclared_fields_separate(data: Path) -> None:
    dataset = taco.open_dataset(data / "shadow.zip")
    raw = dataset.sql(
        'SELECT folder."raster:resolution" AS folder_resolution, '
        'asset."raster:resolution" AS asset_resolution '
        'FROM children AS folder JOIN "children/before" AS asset '
        'ON asset."internal:parent_id" = folder."internal:current_id"'
    )
    assert raw.to_pydict() == {"folder_resolution": [1, 1], "asset_resolution": [3, 3]}


def test_source_list_levels_join_within_each_source(data: Path, tmp_path: Path) -> None:
    second = write("nested", NESTED, nested_samples()[:2], tmp_path / "second.zip")
    dataset = taco.open_dataset([data / "nested.zip", second])

    # The documented parent join pairs rows of one source only, as on a TACOCAT.
    rows = dataset.sql(
        'SELECT s.source_file AS sample_source, b.source_file AS file_source, c."internal:relative_path" AS folder, '
        'b."internal:relative_path" AS file FROM sample AS s '
        'JOIN children AS c ON c."internal:parent_id" = s."internal:current_id" '
        'JOIN "children/before" AS b ON b."internal:parent_id" = c."internal:current_id"'
    ).to_pylist()
    assert len(rows) == 10
    assert all(row["sample_source"] == row["file_source"] for row in rows)
    assert all(row["file"].startswith(row["folder"] + "/") for row in rows)

    # A sample keeps its taco:sample_index as internal:current_id.
    pairs = dataset.sql(
        'SELECT d."taco:sample_index" AS position, s."internal:current_id" AS current '
        "FROM dataset AS d JOIN sample AS s ON s.source_file = d.source_file AND s.id = d.id"
    ).to_pylist()
    assert sorted(row["current"] for row in pairs) == [0, 1, 2, 3, 4]
    assert all(row["position"] == row["current"] for row in pairs)
    children = dataset.sql('SELECT "internal:current_id" AS id FROM children ORDER BY id').column("id").to_pylist()
    assert children == list(range(len(children)))


class Upper(BaseModel):
    Score: float


class Lower(BaseModel):
    score: float


def test_long_view_shadows_a_field_that_differs_only_in_case(tmp_path: Path) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    contract = taco.Contract(
        structure=["a.bin"], metadata=[taco.Level("sample", q=Upper), taco.Level("children", q=Lower)]
    )
    sample = taco.Sample(
        id="s0",
        metadata=taco.Metadata(q=Upper(Score=1.0)),
        assets=[taco.Asset(b"x", path="a.bin", metadata=taco.Metadata(q=Lower(score=2.0)))],
    )
    output = write("case", contract, [sample], tmp_path / "case.zip")

    long = engine.open_reader().execute(native_sql(output, layout="long")).to_arrow_table()
    assert [name for name in long.column_names if name.lower() == "q:score"] == ["q:score"]
    assert long.column("q:score").to_pylist() == [2.0]
    assert taco.read(output).column("q:Score").to_pylist() == [1.0]


def test_single_file_samples(data: Path) -> None:
    dataset = taco.open_dataset(data / "single.zip")
    table = dataset.read()
    assert table.num_rows == 6
    assert all(value.startswith("/vsisubfile/") for value in table.column("data.bin::location").to_pylist())
    assert dataset.sql('SELECT * FROM dataset ORDER BY "taco:sample_index"').equals(table)
    with pytest.raises(ContainerError, match="unknown structure leaf"):
        dataset.read(files=["change.bin"])


def test_folder_and_catalog_locations(data: Path) -> None:
    folder = by_sample(taco.read(data / "nested"))
    assert folder[0]["change.bin::location"] == f"{(data / 'nested').resolve()}/DATA/0/change.bin"
    assert taco.open_dataset(data / "nested").sql("SELECT * FROM dataset").num_rows == 3

    catalog = taco.read(data / "catalog" / ".tacocat")
    assert catalog.num_rows == 3
    assert set(catalog.column("source_file").to_pylist()) == {"part_train.zip", "part_val.zip"}
    locations = catalog.column("change.bin::location").to_pylist()
    assert all(value.split(",", 1)[1].startswith(str((data / "catalog").resolve()) + "/part_") for value in locations)

    catalog_root = taco.read(data / "catalog")
    assert catalog_root.equals(catalog)


def test_catalog_partitions_stay_below_the_catalog(data: Path, tmp_path: Path) -> None:
    escaping = tmp_path / "escaping"
    shutil.copytree(data / "catalog", escaping)
    collection = escaping / ".tacocat" / "COLLECTION.json"
    document = json.loads(collection.read_text())
    document["taco:sources"]["partitions"][0]["file"] = "../../etc/hosts.zip"
    collection.write_text(json.dumps(document))
    for source in (escaping, escaping.as_uri()):
        with pytest.raises(ContainerError, match=r"partition '\.\./\.\./etc/hosts\.zip' is not a normalized relative"):
            taco.open_dataset(source)

    # A row naming a file taco:sources does not list fails the query instead of getting a location.
    stray = tmp_path / "stray"
    shutil.copytree(data / "catalog", stray)
    for path in (stray / ".tacocat").glob("*.parquet"):
        table = pq.read_table(path)
        index = table.schema.get_field_index("internal:source_file")
        files = [
            "../../etc/hosts.zip" if file == "part_train.zip" else file for file in table.column(index).to_pylist()
        ]
        changed = table.set_column(index, table.schema.field(index), pa.array(files, pa.string()))
        pq.write_table(changed.replace_schema_metadata(table.schema.metadata), path)
    dataset = taco.open_dataset(stray)
    with pytest.raises(duckdb.InvalidInputException, match=r"outside taco:sources: \.\./\.\./etc/hosts\.zip"):
        dataset.read()
    assert dataset.sql("SELECT count(*) AS n FROM sample").column("n").to_pylist() == [3]


def test_folder_rows_stay_below_data(data: Path, tmp_path: Path) -> None:
    from taco.reader import engine
    from taco.reader.inspect import native_sql

    stray = tmp_path / "stray"
    shutil.copytree(data / "nested", stray)
    children = stray / "METADATA" / "children.parquet"
    table = pq.read_table(children)
    index = table.schema.get_field_index("internal:relative_path")
    field = table.schema.field(index)
    paths = ["0/../../../etc/hosts" if path == "0/change.bin" else path for path in table.column(index).to_pylist()]
    changed = table.set_column(index, field, pa.array(paths, field.type))
    pq.write_table(changed.replace_schema_metadata(table.schema.metadata), children)

    # A row whose relative path is not normalized fails the query in both views.
    with ranged_origin(tmp_path) as (base, _):
        for source in (str(stray), f"{base}/stray/"):
            with pytest.raises(duckdb.InvalidInputException, match=r"not normalized: 0/\.\./\.\./\.\./etc/hosts"):
                engine.open_reader().execute(native_sql(source, layout="long"))
            dataset = taco.open_dataset(source)
            with pytest.raises(duckdb.InvalidInputException, match="not normalized"):
                dataset.read()
            rows = dataset.sql("SELECT count(*) AS n FROM children").column("n").to_pylist()
            assert rows == [table.num_rows]


def test_contract_errors(data: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(data / "nested", broken)
    (broken / "COLLECTION.json").write_text("{ not json")
    with pytest.raises(ContainerError, match=r"COLLECTION\.json is not valid JSON"):
        taco.open_dataset(broken)

    old = tmp_path / "old"
    shutil.copytree(data / "nested", old)
    document = json.loads((old / "COLLECTION.json").read_text())
    document["taco:version"] = "2.0.0"
    (old / "COLLECTION.json").write_text(json.dumps(document))
    with pytest.raises(ContainerError, match="unsupported TACO version"):
        taco.reader.inspect.levels(old)


def test_collection_json_repeats_no_key_and_fits_doubles(data: Path, tmp_path: Path) -> None:
    # JSON readers keep different values of a repeated key, and 1e400 is no double.
    original = (data / "nested" / "COLLECTION.json").read_text()
    cases = {
        "repeated": (original.replace("{", '{"id": "first", ', 1), 'duplicate key "id"'),
        "nested": (
            original.replace('"taco:metadata": {', '"taco:metadata": {"sample": {}, ', 1),
            'duplicate key "sample"',
        ),
        "overflow": (original.replace("{", '{"x:big": 1e400, ', 1), "number does not fit a double"),
    }
    for name, (text, reason) in cases.items():
        folder = tmp_path / name
        shutil.copytree(data / "nested", folder)
        (folder / "COLLECTION.json").write_text(text)
        for source in (folder, folder.as_uri()):
            with pytest.raises(ContainerError, match=rf"COLLECTION\.json is not valid JSON \({reason} at byte \d+\)"):
                taco.open_dataset(source)


def test_variable_sequence_bounds(tmp_path: Path) -> None:
    from taco.reader import native

    contract = taco.Contract(structure=["img*[1,3].bin"])
    sample = taco.Sample(id="s0", assets=[taco.Asset(b"x", path="img0.bin")])
    folder = write("bounds", contract, [sample], tmp_path / "bounds")
    collection = folder / "COLLECTION.json"
    document = json.loads(collection.read_text())

    def declare(leaf: str) -> None:
        document["taco:structure"] = [leaf]
        collection.write_text(json.dumps(document))

    # The names a sequence produces are checked, not its prefix as a name of its own.
    for leaf in ("img.*[1,3].bin", "img *[1,3].bin", "img*[1,9007199254740991].bin"):
        declare(leaf)
        assert native.NativeDataset(folder).structure == [leaf]
    for leaf in (
        "img*[ 1,3].bin",
        "img*[1, 3].bin",
        "img*[01,3].bin",
        "img*[+1,3].bin",
        "img*[1,9007199254740992].bin",
        "img*[1,18446744073709551616].bin",
    ):
        declare(leaf)
        with pytest.raises(ContainerError, match=re.escape(f"malformed variable leaf '{leaf}'")):
            taco.open_dataset(folder)


def test_file_uris_give_the_locations_of_their_path(tmp_path: Path) -> None:
    # A file:// source is percent-decoded and loses its scheme, ZIP archive parts included.
    root = tmp_path.resolve() / "web data %41#x"
    names = ["with space.bin", "pct%41.bin", "my dir%41/inner.bin"]
    contract = taco.Contract(structure=names)
    sample = taco.Sample(id="s0", assets=[taco.Asset(name.encode(), path=name) for name in names])
    write("names", contract, [sample], root / "names.zip")
    write("names", contract, [sample], root / "names")
    split = taco.Contract(structure=["with space.bin"], metadata=[taco.Level("sample", info=Split)])
    samples = [
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(info=Split(split=split_name, n_images=1)),
            assets=taco.Asset(f"payload {index}".encode(), path="with space.bin"),
        )
        for index, split_name in enumerate(["train", "val"])
    ]
    write("names", split, samples, root / "cat" / "part.zip", partition_by="info:split")

    for path in (root / "names.zip", root / "names", root / "cat"):
        assert "/web%20data%20%2541%23x/" in path.as_uri()
        assert taco.read(path.as_uri()).equals(taco.read(path))

    archive = taco.read((root / "names.zip").as_uri())
    folder = taco.read((root / "names").as_uri())
    for name in names:
        location = archive.column(f"{name}::location")[0].as_py()
        assert location.endswith(f",{root / 'names.zip'}")
        assert read_location(location) == name.encode()
        assert read_location(folder.column(f"{name}::location")[0].as_py()) == name.encode()
    for row in taco.read((root / "cat").as_uri()).to_pylist():
        assert row["with space.bin::location"].endswith(f",{root / 'cat' / row['source_file']}")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_a_fork_while_another_thread_opens_a_local_zip(data: Path, tmp_path: Path) -> None:
    from taco.reader import native

    busy, other = tmp_path / "busy.zip", tmp_path / "other.zip"
    for path in (busy, other):
        shutil.copy(data / "nested.zip", path)
    parent = os.getpid()
    entered, released = threading.Event(), threading.Event()

    @native._ffi.callback("void(const char*, uint64_t, uint64_t, void*)")
    def pause(phase: object, done: int, total: int, user: object) -> None:
        if os.getpid() == parent and not entered.is_set():
            entered.set()
            released.wait(30)

    library = native._load()
    library.taco_set_progress(pause, native._ffi.NULL)
    opener = threading.Thread(target=native.NativeDataset, args=(busy,))
    opener.start()
    try:
        assert entered.wait(30)
        # The opener waits in its progress callback, halfway through extracting.
        process = multiprocessing.get_context("fork").Process(target=native.NativeDataset, args=(other,))
        process.start()
        process.join(30)
        if process.is_alive():
            process.kill()
            process.join()
            pytest.fail("the forked child hung")
        assert process.exitcode == 0
    finally:
        released.set()
        opener.join()
        library.taco_set_progress(native._progress_callback, native._ffi.NULL)
    assert native.NativeDataset(busy).levels == ["sample", "children", "children/after", "children/before"]


def test_remote_folder_cache_recovers_when_a_metadata_level_disappears(data: Path, tmp_path: Path) -> None:
    source = tmp_path / "moving"
    shutil.copytree(data / "nested", source)
    uri = source.as_uri()

    assert taco.open_dataset(uri).contract.levels == ("sample", "children", "children/before", "children/after")

    shutil.rmtree(source)
    shutil.copytree(data / "single", source)

    dataset = taco.open_dataset(uri)
    assert dataset.contract.levels == ("sample", "children")
    assert dataset.read().num_rows == 6


@contextmanager
def ranged_origin(root: Path) -> Iterator[tuple[str, list[tuple[str, str]]]]:
    """Serve root with single byte ranges and record each request as (path, range)."""
    requests: list[tuple[str, str]] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(root), **kwargs)  # type: ignore[arg-type]

        def do_HEAD(self) -> None:
            requests.append((self.path, "HEAD"))
            super().do_HEAD()

        def do_GET(self) -> None:
            header = self.headers.get("Range")
            requests.append((self.path, header or "GET"))
            target = Path(self.translate_path(self.path))
            if header is None or not target.is_file():
                super().do_GET()
                return
            data = target.read_bytes()
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
            assert match is not None
            first = int(match[1])
            last = min(int(match[2]) if match[2] else len(data) - 1, len(data) - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {first}-{last}/{len(data)}")
            self.send_header("Content-Length", str(last - first + 1))
            self.end_headers()
            self.wfile.write(data[first : last + 1])

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", requests
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def size_probes(requests: list[tuple[str, str]], path: str) -> int:
    return sum(1 for seen, kind in requests if seen == path and kind in ("HEAD", "bytes=0-0"))


def test_remote_open_sizes_each_object_once(data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TACO_CACHE_DIR", str(tmp_path / "cache"))
    with ranged_origin(data) as (base, requests):
        taco.open_dataset(f"{base}/nested.zip")
        assert size_probes(requests, "/nested.zip") == 1
        requests.clear()
        # A cached archive is checked against the CoZIP integrity hash at bytes 43 to 50.
        taco.open_dataset(f"{base}/nested.zip")
        assert requests == [("/nested.zip", "bytes=43-50")]

        requests.clear()
        taco.open_dataset(f"{base}/nested/")
        assert size_probes(requests, "/nested/COLLECTION.json") == 1


def test_http_paths_are_percent_encoded(tmp_path: Path) -> None:
    # Each appended component is encoded like encodeURIComponent; the URL given is kept.
    encoded = {
        "with space.bin": "with%20space.bin",
        "hash#1.bin": "hash%231.bin",
        "pct%41.bin": "pct%2541.bin",
        "it's (1).bin": "it's%20(1).bin",
        "my dir/inner.bin": "my%20dir/inner.bin",
    }
    sample = taco.Sample(id="s0", assets=[taco.Asset(name.encode(), path=name) for name in encoded])
    write("names", taco.Contract(structure=list(encoded)), [sample], tmp_path / "web data" / "names")
    split = taco.Contract(structure=["with space.bin"], metadata=[taco.Level("sample", info=Split)])
    samples = [
        taco.Sample(
            id=f"s{index}",
            metadata=taco.Metadata(info=Split(split=split_name, n_images=1)),
            assets=taco.Asset(f"payload {index}".encode(), path="with space.bin"),
        )
        for index, split_name in enumerate(["train", "val"])
    ]
    write("names", split, samples, tmp_path / "web data" / "my cat#1.zip", partition_by="info:split")

    with ranged_origin(tmp_path) as (base, requests):
        folder = taco.read(f"{base}/web%20data/names/")
        assert ("/web%20data/names/METADATA/children__my%20dir.parquet", "bytes=0-0") in requests
        for name, path in encoded.items():
            location = folder.column(f"{name}::location")[0].as_py()
            assert location == f"/vsicurl/{base}/web%20data/names/DATA/0/{path}"
            with urllib.request.urlopen(location.removeprefix("/vsicurl/")) as response:
                assert response.read() == name.encode()

        catalog = taco.read(f"{base}/web%20data/.tacocat")
        for row in catalog.to_pylist():
            match = re.fullmatch(r"/vsisubfile/(\d+)_(\d+),/vsicurl/(.+)", row["with space.bin::location"])
            assert match is not None
            assert match[3] == f"{base}/web%20data/" + row["source_file"].replace(" ", "%20").replace("#", "%23")
            first, size = int(match[1]), int(match[2])
            ranged = urllib.request.Request(match[3], headers={"Range": f"bytes={first}-{first + size - 1}"})
            with urllib.request.urlopen(ranged) as response:
                assert response.read() == f"payload {row['id'].removeprefix('s')}".encode()


@pytest.mark.skipif(
    os.environ.get("TACO_TEST_REMOTE") != "1",
    reason="set TACO_TEST_REMOTE=1 to read from Hugging Face and Source Coop",
)
def test_remote_datasets() -> None:
    base = "hf://datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection"
    archive = taco.open_dataset(f"{base}/single-zip/dataset.zip").sql("SELECT * FROM dataset")
    assert archive.num_rows == 6
    assert (
        archive.column("change.rumi::location")[0]
        .as_py()
        .endswith(",/vsihf/datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/single-zip/dataset.zip")
    )
    assert taco.read(f"{base}/by-split/.tacocat").num_rows == 6
    folder = taco.read(f"{base}/folder")
    assert folder.num_rows == 6
    assert (
        folder.column("change.rumi")[0]
        .as_py()
        .startswith("/vsihf/datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/folder/DATA/")
    )

    mirror = "source://asterisk-labs/taco-api-fixtures/data/04-change-detection"
    catalog = taco.open_dataset(f"{mirror}/by-split/.tacocat").sql("SELECT * FROM dataset")
    assert catalog.num_rows == 6
    location = catalog.column("change.rumi::location")[0].as_py()
    assert ",/vsisource/asterisk-labs/taco-api-fixtures/data/04-change-detection/by-split/" in location
    assert taco.read(f"{mirror}/folder").num_rows == 6
