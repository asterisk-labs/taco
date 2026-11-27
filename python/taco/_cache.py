from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar
from urllib.parse import urlparse

T = TypeVar("T")

# Entries follow the core's layout, so TACO_CACHE_SIZE and its eviction count them.
_STAMP = "taco-cache.json"
_CACHEDIR_TAG = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This directory holds metadata that taco can download again.\n"
    "# See https://bford.info/cachedir/\n"
)
_RETRY_DELAYS = (1, 2, 4)
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)
_WINDOWS = os.name == "nt"
# Entries this process downloaded again for TACO_CACHE_REFRESH. A refresh happens
# once, as GeoEnrich asks for its index once per batch.
_refreshed: set[Path] = set()


def cache_root() -> Path:
    """The cache directory, resolved in the same order as the core."""
    configured = os.environ.get("TACO_CACHE_DIR")
    if configured:
        return Path(configured)
    if _WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local, "taco", "cache")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            return Path(xdg, "taco")
        home = os.environ.get("HOME")
        if home:
            return Path(home, ".cache", "taco")
    return Path(".taco-cache")


def cached_download(url: str, label: str) -> Path:
    """A local copy of url, downloaded again when the origin changes."""
    entry = cache_root() / f"{label}-{hashlib.sha256(url.encode()).hexdigest()[:12]}"
    name = PurePosixPath(urlparse(url).path).name or "data"
    key = _retrying(lambda: _validator(url))
    stamp = _read_stamp(entry)
    target = entry / name
    refresh = bool(os.environ.get("TACO_CACHE_REFRESH")) and entry not in _refreshed
    if not refresh and _reusable(stamp, key, target):
        assert stamp is not None
        stamp["opened"] = _now()
        # A read-only cache still serves its entries.
        with contextlib.suppress(OSError):
            _write_atomically(entry / _STAMP, json.dumps(stamp))
        return target
    path = _store(url, entry, name, key)
    if refresh:
        _refreshed.add(entry)
    return path


def _reusable(stamp: dict[str, Any] | None, key: str, target: Path) -> bool:
    if stamp is None or not key:
        return False
    return stamp.get("key") == key and target.is_file() and target.stat().st_size == stamp.get("size")


def _validator(url: str) -> str:
    with _open(url, "HEAD") as response:
        headers = response.headers
    etag = headers.get("ETag", "")
    size = headers.get("Content-Length", "")
    return f"{etag}|{size}" if etag or size else ""


def _store(url: str, entry: Path, name: str, key: str) -> Path:
    root = entry.parent
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "CACHEDIR.TAG").exists():
        _write_atomically(root / "CACHEDIR.TAG", _CACHEDIR_TAG)
    # The entry is assembled aside and swapped in, so a reader never sees half a file.
    staging = root / f".tmp-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        _retrying(lambda: _download(url, staging / name))
        now = _now()
        stamp = {"source": url, "container": "file", "key": key, "size": (staging / name).stat().st_size}
        _write_atomically(staging / _STAMP, json.dumps(stamp | {"created": now, "opened": now}))
        _replace_entry(staging, entry, key, name)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return entry / name


def _download(url: str, target: Path) -> None:
    with _open(url, "GET") as response, target.open("wb") as stream:
        shutil.copyfileobj(response, stream, 1 << 20)
        expected = response.headers.get("Content-Length")
    # urllib returns a short body without an error when the connection drops.
    size = target.stat().st_size
    if expected is not None and size != int(expected):
        raise http.client.IncompleteRead(b"", int(expected) - size)


def _open(url: str, method: str) -> Any:
    # Source Cooperative rejects requests without a User-Agent.
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "taco-eo"})
    return urllib.request.urlopen(request, timeout=60)


def _retrying(action: Callable[[], T]) -> T:
    """Retry network failures and server errors; other HTTP errors fail at once."""
    for delay in _RETRY_DELAYS:
        try:
            return action()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise
        except _NETWORK_ERRORS:
            pass
        time.sleep(delay)
    return action()


def _read_stamp(entry: Path) -> dict[str, Any] | None:
    try:
        stamp = json.loads((entry / _STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stamp if isinstance(stamp, dict) else None


def _replace_entry(staging: Path, entry: Path, key: str, name: str) -> None:
    previous = entry.with_name(f".trash-{uuid.uuid4().hex}")
    moved = False
    try:
        try:
            entry.rename(previous)
            moved = True
        except FileNotFoundError:
            pass
        try:
            staging.rename(entry)
        except OSError:
            if _reusable(_read_stamp(entry), key, entry / name):
                shutil.rmtree(staging, ignore_errors=True)
                return
            if moved and not entry.exists():
                previous.rename(entry)
                moved = False
            raise
    finally:
        if moved:
            shutil.rmtree(previous, ignore_errors=True)


def _write_atomically(target: Path, text: str) -> None:
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
    temporary.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    temporary.replace(target)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["cache_root", "cached_download"]
