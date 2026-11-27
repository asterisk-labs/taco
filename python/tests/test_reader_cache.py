from __future__ import annotations

import io
from email.message import Message

import pytest

from taco import _cache as cache


class Response(io.BytesIO):
    def __init__(self, body: bytes, method: str, etag: str) -> None:
        super().__init__(b"" if method == "HEAD" else body)
        self.headers = Message()
        self.headers["Content-Length"] = str(len(body))
        self.headers["ETag"] = etag


def test_cache_refresh_downloads_an_index_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    origin = {"body": b"index", "etag": '"1"'}
    downloads: list[str] = []

    def fake_open(url: str, method: str) -> Response:
        if method == "GET":
            downloads.append(url)
        return Response(origin["body"], method, origin["etag"])

    monkeypatch.setattr(cache, "_open", fake_open)
    url = "https://example.com/index/global.parquet"
    first = cache.cached_download(url, "majortom-index")
    monkeypatch.setenv("TACO_CACHE_REFRESH", "1")
    # GeoEnrich asks for its index once per batch.
    assert [cache.cached_download(url, "majortom-index") for _ in range(4)] == [first] * 4
    assert len(downloads) == 2

    # The refreshed copy is still checked against its origin.
    origin.update(body=b"changed", etag='"2"')
    assert cache.cached_download(url, "majortom-index").read_bytes() == b"changed"
    assert len(downloads) == 3
