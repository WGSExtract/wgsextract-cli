"""Bounded, resumable upstream transfers for the release-data publisher."""

from __future__ import annotations

import hashlib
import logging
import random
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

USER_AGENT = "wgsextract-cli-mirror"
ATTEMPTS = 5
MAX_FULL_DOWNLOAD_BYTES = 10 * 1024**3
DISK_RESERVE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class Source:
    url: str
    size: int
    etag: str | None
    modified: str | None

    def headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if self.etag and not self.etag.startswith("W/"):
            headers["If-Match"] = self.etag
        elif self.modified:
            headers["If-Unmodified-Since"] = self.modified
        return headers


class RangesUnsupported(OSError):
    pass


def retry_after_seconds(error: OSError | HTTPException) -> float:
    if not isinstance(error, HTTPError) or error.code not in (429, 503):
        return 0.0
    value = error.headers.get("Retry-After", "") if error.headers else ""
    try:
        return max(0.0, float(int(value)))
    except (ValueError, TypeError, OverflowError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0.0


@dataclass
class RetryPolicy:
    stalled: int = 0

    def pause(
        self, error: OSError | HTTPException, *, progress: bool, label: str
    ) -> bool:
        retry_after = retry_after_seconds(error)
        if isinstance(error, HTTPError):
            error.close()
        self.stalled = 0 if progress else self.stalled + 1
        if self.stalled >= ATTEMPTS:
            return False
        base = min(60.0, 2.0 ** max(0, self.stalled - 1))
        delay = max(random.uniform(base, min(60.0, base * 1.25)), retry_after)
        logging.warning(
            "%s interrupted (%s); retrying in %.1f s (%s consecutive stalled failures)",
            label,
            error,
            delay,
            self.stalled,
        )
        time.sleep(delay)
        return True


def source_info(url: str) -> Source:
    with urlopen(
        Request(url, method="HEAD", headers={"User-Agent": USER_AGENT}), timeout=120
    ) as response:
        size = int(response.headers.get("Content-Length") or 0)
        if size <= 0:
            raise ValueError(f"Cannot safely publish a resource of unknown size: {url}")
        return Source(
            url,
            size,
            response.headers.get("ETag"),
            response.headers.get("Last-Modified"),
        )


def fetch_range(source: Source, path: Path, start: int, end: int) -> None:
    """Close each response before upload; resume a short read at its exact byte."""
    # Prior-process bytes have no saved source identity; only resume this transfer.
    path.write_bytes(b"")
    retry = RetryPolicy()
    while True:
        received = path.stat().st_size if path.exists() else 0
        if received == end - start + 1:
            return
        headers = source.headers() | {"Range": f"bytes={start + received}-{end}"}
        try:
            with urlopen(Request(source.url, headers=headers), timeout=120) as response:
                if response.status == 200:
                    raise RangesUnsupported(source.url)
                expected_range = f"bytes {start + received}-{end}/{source.size}"
                if (
                    response.status != 206
                    or response.headers.get("Content-Range") != expected_range
                ):
                    raise ValueError(
                        f"Invalid Range response for {source.url}: expected {expected_range}"
                    )
                if (
                    source.etag
                    and response.headers.get("ETag", source.etag) != source.etag
                ):
                    raise ValueError("Upstream changed its ETag during publication")
                remaining = end - start + 1 - received
                with path.open("ab") as target:
                    while remaining:
                        chunk = response.read(min(1 << 20, remaining))
                        if not chunk:
                            raise OSError("Short upstream Range response")
                        target.write(chunk)
                        remaining -= len(chunk)
                return
        except RangesUnsupported:
            raise
        except HTTPError as exc:
            if exc.code < 500 and exc.code not in (408, 429):
                raise
            failure: OSError | HTTPException = exc
        except (OSError, HTTPException) as exc:
            failure = exc
        progress = path.stat().st_size > received
        if not retry.pause(failure, progress=progress, label=f"Range {start}-{end}"):
            break
    raise OSError(
        f"Could not finish Range {start}-{end} from {source.url}"
    ) from failure


def fetch_whole(source: Source, path: Path, part_size: int) -> None:
    required = source.size + min(source.size, part_size) + DISK_RESERVE_BYTES
    if (
        source.size > MAX_FULL_DOWNLOAD_BYTES
        or shutil.disk_usage(path.parent).free < required
    ):
        raise ValueError(
            f"RANGE_REQUIRED: upstream ignores Range; {source.size:,} bytes exceed "
            "the 10 GiB whole-file fallback or available disk budget. Use a range-capable upstream."
        )
    retry = RetryPolicy()
    high_water = 0
    while True:
        received = 0
        try:
            with (
                urlopen(
                    Request(source.url, headers=source.headers()), timeout=120
                ) as response,
                path.open("wb") as target,
            ):
                if (
                    source.etag
                    and response.headers.get("ETag", source.etag) != source.etag
                ):
                    raise ValueError("Upstream changed its ETag during publication")
                remaining = source.size
                while remaining:
                    chunk = response.read(min(1 << 20, remaining))
                    if not chunk:
                        raise OSError("Short whole-file upstream response")
                    target.write(chunk)
                    remaining -= len(chunk)
                    received += len(chunk)
                if response.read(1):
                    raise ValueError("Upstream exceeded its advertised size")
            return
        except HTTPError as exc:
            if exc.code < 500 and exc.code not in (408, 429):
                raise
            failure: OSError | HTTPException = exc
        except (OSError, HTTPException) as exc:
            failure = exc
        progress = received > high_water
        high_water = max(high_water, received)
        if not retry.pause(failure, progress=progress, label="Whole-file transfer"):
            break
    raise OSError(
        f"Could not finish whole-file transfer from {source.url}"
    ) from failure


def transfer_resource(
    url: str,
    out: Path,
    stem: str,
    part_size: int,
    accept_part: Callable[[Path, str, int], None],
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    source = source_info(url)
    whole = hashlib.sha256()
    full = out / f"{stem}.upstream"

    def emit(path: Path, expected_size: int) -> None:
        if path.stat().st_size != expected_size:
            raise ValueError(f"Part size mismatch: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as data:
            for chunk in iter(lambda: data.read(1 << 20), b""):
                digest.update(chunk)
                whole.update(chunk)
        accept_part(path, digest.hexdigest(), expected_size)

    try:
        for index, start in enumerate(range(0, source.size, part_size)):
            end = min(start + part_size, source.size) - 1
            part = out / f"{stem}.part{index:04d}"
            try:
                fetch_range(source, part, start, end)
                emit(part, end - start + 1)
            except RangesUnsupported:
                if start:
                    raise ValueError(
                        "RANGE_REQUIRED: upstream stopped honoring Range during publication"
                    ) from None
                fetch_whole(source, full, part_size)
                with full.open("rb") as data:
                    for fallback_index, offset in enumerate(
                        range(0, source.size, part_size)
                    ):
                        fallback_part = out / f"{stem}.part{fallback_index:04d}"
                        try:
                            with fallback_part.open("wb") as target:
                                remaining = min(part_size, source.size - offset)
                                while remaining:
                                    chunk = data.read(min(1 << 20, remaining))
                                    if not chunk:
                                        raise OSError("Short staged upstream file")
                                    target.write(chunk)
                                    remaining -= len(chunk)
                            emit(fallback_part, min(part_size, source.size - offset))
                        finally:
                            fallback_part.unlink(missing_ok=True)
                break
            finally:
                part.unlink(missing_ok=True)
    finally:
        full.unlink(missing_ok=True)
    digest = whole.hexdigest()
    if expected_sha256 and digest != expected_sha256.lower():
        raise ValueError("Whole resource SHA-256 differs from the pinned snapshot")
    return {
        "size": source.size,
        "sha256": digest,
        "upstream_last_modified": source.modified,
        "upstream_etag": source.etag,
    }
