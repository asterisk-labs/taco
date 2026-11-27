from __future__ import annotations

import gc
import http.client
import multiprocessing
import os
import re
import threading
import urllib.error
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import BaseModel

import taco
from taco import _cache as cache
from taco.reader import engine, native


@dataclass
class Origin:
    url: str
    root: Path
    downloads: int = 0
    # Status codes answered, in order, before files are served normally.
    failures: list[int] = field(default_factory=list)
    # Bytes to cut from the body while still announcing the full length.
    truncate: int = 0
    etag: str | None = None
    validators: bool = True


@contextmanager
def origin(root: Path) -> Iterator[Origin]:
    state = Origin("", root)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(root), **kwargs)  # type: ignore[arg-type]

        def do_GET(self) -> None:
            if state.failures:
                self.send_error(state.failures.pop(0))
                return
            state.downloads += 1
            if not state.truncate:
                super().do_GET()
                return
            data = Path(self.translate_path(self.path)).read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Last-Modified", "Mon, 28 Sep 2026 00:00:00 GMT")
            self.end_headers()
            self.wfile.write(data[: -state.truncate])

        def do_HEAD(self) -> None:
            if state.validators:
                super().do_HEAD()
            else:
                self.send_response(200)
                self.end_headers()

        def end_headers(self) -> None:
            if state.etag is not None:
                self.send_header("ETag", state.etag)
            super().end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        yield state
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "cache"
    monkeypatch.setenv("TACO_CACHE_DIR", str(directory))
    monkeypatch.delenv("TACO_CACHE_REFRESH", raising=False)
    monkeypatch.setattr(cache.time, "sleep", lambda seconds: None)
    return directory


def served(tmp_path: Path, content: bytes = b"index") -> Path:
    root = tmp_path / "origin"
    root.mkdir(exist_ok=True)
    (root / "global.parquet").write_bytes(content)
    return root


def test_cache_root_follows_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TACO_CACHE_DIR", "XDG_CACHE_HOME", "HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cache, "_WINDOWS", False)
    assert cache.cache_root() == Path(".taco-cache")
    monkeypatch.setenv("HOME", "/home/me")
    assert cache.cache_root() == Path("/home/me/.cache/taco")
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")
    assert cache.cache_root() == Path("/xdg/taco")
    monkeypatch.setenv("TACO_CACHE_DIR", "/data/taco")
    assert cache.cache_root() == Path("/data/taco")
    monkeypatch.delenv("TACO_CACHE_DIR")
    monkeypatch.setattr(cache, "_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/me/AppData/Local")
    assert cache.cache_root() == Path("C:/Users/me/AppData/Local", "taco", "cache")


def test_cached_download_reuses_an_unchanged_copy(tmp_path: Path, cache_dir: Path) -> None:
    with origin(served(tmp_path)) as server:
        first = cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
        second = cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
    assert first == second
    assert first.read_bytes() == b"index"
    assert first.parent.parent == cache_dir
    assert server.downloads == 1


def test_cached_download_refreshes_a_changed_origin(tmp_path: Path, cache_dir: Path) -> None:
    root = served(tmp_path)
    with origin(root) as server:
        server.etag = '"first"'
        cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
        server.etag = '"second"'
        (root / "global.parquet").write_bytes(b"other")
        path = cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
    assert path.read_bytes() == b"other"
    assert server.downloads == 2
    assert sorted(item.name for item in cache_dir.iterdir()) == ["CACHEDIR.TAG", path.parent.name]


def test_cached_download_honours_cache_refresh(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with origin(served(tmp_path)) as server:
        cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
        monkeypatch.setenv("TACO_CACHE_REFRESH", "1")
        cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
    assert server.downloads == 2


def test_cached_download_without_validators_downloads_each_time(tmp_path: Path, cache_dir: Path) -> None:
    with origin(served(tmp_path)) as server:
        server.validators = False
        cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
        cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
    assert server.downloads == 2


def test_cached_download_does_not_serve_an_unvalidated_copy(tmp_path: Path, cache_dir: Path) -> None:
    root = served(tmp_path)
    with origin(root) as server:
        url = f"{server.url}/global.parquet"
        path = cache.cached_download(url, "majortom-index")
    assert path.is_file()
    with pytest.raises(urllib.error.URLError):
        cache.cached_download(url, "majortom-index")


def test_cached_download_retries_server_errors(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(cache.time, "sleep", sleeps.append)
    with origin(served(tmp_path)) as server:
        server.failures = [503, 429]
        path = cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
    assert path.read_bytes() == b"index"
    assert sleeps == [1, 2]


def test_cached_download_does_not_retry_missing_files(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(cache.time, "sleep", sleeps.append)
    with origin(served(tmp_path)) as server, pytest.raises(urllib.error.HTTPError, match="404"):
        cache.cached_download(f"{server.url}/missing.parquet", "majortom-index")
    assert sleeps == []


def test_interrupted_download_leaves_no_entry(tmp_path: Path, cache_dir: Path) -> None:
    with origin(served(tmp_path)) as server:
        server.truncate = 2
        with pytest.raises(http.client.IncompleteRead):
            cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
    assert server.downloads == 4
    assert [item.name for item in cache_dir.iterdir()] == ["CACHEDIR.TAG"]


def test_the_core_evicts_cached_downloads_like_its_own_entries(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch, collection: taco.Collection, make_sample
) -> None:
    root = served(tmp_path)
    with taco.open_writer(collection, root / "dataset.zip") as writer:
        writer.add(make_sample(0))
        writer.run()
    with origin(root) as server:
        index = cache.cached_download(f"{server.url}/global.parquet", "majortom-index")
        monkeypatch.setenv("TACO_CACHE_SIZE", "1")
        taco.open_dataset(f"{server.url}/dataset.zip")
    assert not index.exists()
    # The dataset's snapshot is not an entry.
    entries = [item for item in cache_dir.iterdir() if item.is_dir() and not item.name.startswith(".open-")]
    assert len(entries) == 1


def test_local_archives_stay_out_of_the_cache(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "local.zip") as writer:
        writer.add(make_sample(0))
        writer.run()
    assert taco.open_dataset(tmp_path / "local.zip").read().num_rows == 1
    root = cache.cache_root()
    assert not root.exists() or not any(root.iterdir())


def test_local_archive_extraction_survives_close(tmp_path: Path, collection: taco.Collection, make_sample) -> None:
    with taco.open_writer(collection, tmp_path / "local.zip") as writer:
        writer.add(make_sample(0))
        writer.run()
    dataset = taco.open_dataset(tmp_path / "local.zip")
    sql = native.sql(dataset._opened, idx=None, level="sample", pivoted=True, files=None, location=False)
    match = re.search(r"read_parquet\('([^']+)'", sql)
    assert match is not None
    extraction = Path(match[1]).parent.parent
    assert extraction.is_dir()
    del dataset
    gc.collect()
    assert extraction.is_dir()


def test_concurrent_downloads_leave_one_valid_entry(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2)
    download = cache._download

    def synchronized(url: str, target: Path) -> None:
        download(url, target)
        barrier.wait()

    monkeypatch.setattr(cache, "_download", synchronized)
    with origin(served(tmp_path)) as server, ThreadPoolExecutor(max_workers=2) as executor:
        url = f"{server.url}/global.parquet"
        paths = list(executor.map(lambda _: cache.cached_download(url, "majortom-index"), range(2)))

    assert paths[0] == paths[1]
    assert paths[0].read_bytes() == b"index"
    assert len([item for item in cache_dir.iterdir() if item.is_dir()]) == 1


def test_failed_entry_swap_restores_the_previous_copy(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = served(tmp_path)
    with origin(root) as server:
        url = f"{server.url}/global.parquet"
        path = cache.cached_download(url, "majortom-index")
        monkeypatch.setenv("TACO_CACHE_REFRESH", "1")
        rename = Path.rename

        def fail_publish(source: Path, target: Path) -> Path:
            if source.name.startswith(".tmp-"):
                raise OSError("publish failed")
            return rename(source, target)

        monkeypatch.setattr(Path, "rename", fail_publish)
        with pytest.raises(OSError, match="publish failed"):
            cache.cached_download(url, "majortom-index")

    assert path.read_bytes() == b"index"
    assert sorted(item.name for item in cache_dir.iterdir()) == ["CACHEDIR.TAG", path.parent.name]


def test_geoenrich_queries_a_cached_copy_of_a_remote_index(tmp_path: Path, cache_dir: Path) -> None:
    root = tmp_path / "origin"
    root.mkdir()
    pq.write_table(
        pa.table({"id": ["MT10km_0000U_0000R"], "geoenrich:elevation": pa.array([12.25], type=pa.float32())}),
        root / "global.parquet",
    )
    with origin(root) as server:
        url = f"{server.url}/global.parquet"
        extension = taco.extensions.sample.geoenrich.GeoEnrich(["elevation"], index_url=url)
        codes = {"majortom:code": ["MT10km_0000U_0000R"]}
        assert extension.compute(codes) == {"elevation": [12.25]}
        assert extension.compute(codes) == {"elevation": [12.25]}
    assert server.downloads == 1
    # The dataset records the origin, not the local copy.
    assert extension.collection_metadata()["index_url"] == url


class Version(BaseModel):
    label: str


def publish(path: Path, payloads: dict[str, bytes]) -> str:
    """Write a ZIP with one sample per label at path and return its file:// URI."""
    collection = taco.Collection(
        contract=taco.Contract(structure=["data.bin"], metadata=[taco.Level("sample", version=Version)]),
        id="published",
        description="Republished at one URI",
        licenses=["MIT"],
        providers=["TACO tests"],
    )
    samples = [
        taco.Sample(
            id=f"s{index}",
            assets=taco.Asset(payload, path="data.bin"),
            metadata=taco.Metadata(version=Version(label=label)),
        )
        for index, (label, payload) in enumerate(payloads.items())
    ]
    with taco.open_writer(collection, path, overwrite=True) as writer:
        writer.extend(samples)
        writer.run()
    return path.as_uri()


def labels(dataset: taco.Dataset) -> list[str]:
    query = 'SELECT "version:label" AS label FROM dataset ORDER BY "taco:sample_index"'
    return dataset.sql(query).column("label").to_pylist()


def snapshots(root: Path) -> list[Path]:
    return sorted(root.glob(".open-*"))


def test_a_republished_archive_of_the_same_size_is_read_again(tmp_path: Path, cache_dir: Path) -> None:
    archive = tmp_path / "remote.zip"
    uri = publish(archive, {"first": b"A" * 50, "again": b"B" * 70})
    size = archive.stat().st_size
    assert labels(taco.open_dataset(uri)) == ["first", "again"]

    # Padding makes archive sizes equal; the integrity hash distinguishes them.
    publish(archive, {"second": b"B" * 70, "later": b"A" * 50})
    assert archive.stat().st_size == size
    dataset = taco.open_dataset(uri)
    assert labels(dataset) == ["second", "later"]
    data = archive.read_bytes()
    locations = dataset.read().column("data.bin::location").to_pylist()
    for location, expected in zip(locations, [b"B" * 70, b"A" * 50], strict=True):
        match = re.match(r"/vsisubfile/(\d+)_(\d+),", location)
        assert match is not None
        offset, length = int(match[1]), int(match[2])
        assert data[offset : offset + length] == expected


def test_an_open_remote_dataset_is_a_snapshot(tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "remote.zip"
    uri = publish(archive, {"old": b"o", "older": b"p"})
    opened = taco.open_dataset(uri)
    publish(archive, {"new": b"n", "newer": b"m", "newest": b"l"})
    assert labels(taco.open_dataset(uri)) == ["new", "newer", "newest"]
    assert labels(opened) == ["old", "older"]

    monkeypatch.setenv("TACO_CACHE_REFRESH", "1")
    assert labels(taco.open_dataset(uri)) == ["new", "newer", "newest"]
    monkeypatch.delenv("TACO_CACHE_REFRESH")
    assert labels(opened) == ["old", "older"]

    # Another dataset over the size cap evicts the entry but not the snapshot.
    monkeypatch.setenv("TACO_CACHE_SIZE", "1")
    other = publish(tmp_path / "other.zip", {"other": b"x"})
    assert labels(taco.open_dataset(other)) == ["other"]
    entries = [item for item in cache_dir.iterdir() if item.is_dir() and not item.name.startswith(".open-")]
    assert len(entries) == 1
    assert labels(opened) == ["old", "older"]
    assert opened.read().num_rows == 2

    # SQL from a closed dataset still uses its process-owned snapshot.
    sql = native.sql(opened._opened, idx=None, level="sample", pivoted=True, files=None, location=False)
    del opened
    gc.collect()
    assert engine.open_reader().execute(sql).to_arrow_table().num_rows == 2
    count = len(snapshots(cache_dir))
    for _ in range(3):
        assert labels(taco.open_dataset(other)) == ["other"]
    assert len(snapshots(cache_dir)) == count


def _close_in_child(dataset: taco.Dataset) -> None:
    for handle in dataset._opened:
        native._ffi.release(handle._handle)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_a_forked_child_keeps_the_snapshot_of_its_parent(tmp_path: Path, cache_dir: Path) -> None:
    dataset = taco.open_dataset(publish(tmp_path / "remote.zip", {"a": b"a", "b": b"b"}))
    [snapshot] = snapshots(cache_dir)
    process = multiprocessing.get_context("fork").Process(target=_close_in_child, args=(dataset,))
    process.start()
    process.join(30)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("the forked child hung")
    assert process.exitcode == 0
    assert snapshot.is_dir()
    assert labels(dataset) == ["a", "b"]
