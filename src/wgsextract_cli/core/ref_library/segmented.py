"""Large downloads over several connections.

Some servers cap each connection: Ensembl serves its 27.6 GB VEP cache at about
0.4 MB/s per connection, 19 hours over one. A file of at least
SEGMENTED_MIN_SIZE whose server takes byte ranges is fetched in SEGMENT_SIZE
pieces, CONNECTIONS at a time, straight into dest.partial. dest.partial.segments
records the source URL's digest, the file's Revision and how much of each piece
has arrived, so an interrupted download resumes every piece where it stopped,
but only from the same version of the file (#1225).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Future, wait
from dataclasses import dataclass, field
from email.message import Message
from email.utils import parsedate_to_datetime
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from wgsextract_cli.core.cancellable_http import cancellable_http
from wgsextract_cli.core.download_progress import (
    CancelEvent,
    DownloadCancelled,
    DownloadIncomplete,
    PercentProgressLogger,
    ProgressCallback,
    redact_url,
    redact_urls,
    retryable_http_status,
)
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor

CONNECTIONS = 8
SEGMENTED_MIN_SIZE = 1024**3
SEGMENT_SIZE = 256 * 1024**2
ATTEMPTS = 5
TIMEOUT_SECONDS = 60
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)
# Hosts that refuse an outdated browser's User-Agent (403) but serve a tool's.
_TOOL_AGENT_HOSTS = ("zenodo.org",)
# Hosts that refuse both but serve Python's own (REVEL's, checked 2026-11-16).
_PYTHON_AGENT_HOSTS = ("rothsj06.dmz.hpc.mssm.edu",)


def user_agent(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()

    def listed(hosts: tuple[str, ...]) -> bool:
        return any(host == h or host.endswith(f".{h}") for h in hosts)

    if listed(_TOOL_AGENT_HOSTS):
        return "wgsextract-cli"
    if listed(_PYTHON_AGENT_HOSTS):
        return f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}"
    return USER_AGENT


_CHUNK = 1024**2
_RESUMABLE = (DownloadIncomplete, IncompleteRead, TimeoutError, ConnectionError)


def _backoff(attempt: int) -> float:
    """Seconds to wait before a piece's next attempt."""
    return float(min(30, 2**attempt))


def _transient(error: Exception) -> bool:
    """A failure worth retrying the piece for: a dropped or refused connection,
    or a server that is busy (429, 5xx), not one that refuses the file (4xx)."""
    if isinstance(error, HTTPError):
        return retryable_http_status(error.code)
    return isinstance(error, (*_RESUMABLE, URLError))


class RangesUnsupported(Exception):
    """The server answered a byte-range request with the whole file."""


class RemoteChanged(Exception):
    """The file changed on the server while its pieces were being fetched."""


_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)")
_DIGITS = re.compile(r"[0-9]+")


def _strong_etag(value: str) -> str:
    """A strong ETag, else "": a weak one (W/"...") promises no byte-for-byte
    match."""
    value = value.strip()
    if not value.isascii() or not value.isprintable():
        return ""
    return value if re.fullmatch(r'"[^"]*"', value) else ""


def _http_date(value: str) -> str:
    value = value.strip()
    if not value.isascii() or not value.isprintable():
        return ""
    try:
        parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError, IndexError):
        return ""
    return value


def _strong_date(last_modified: str, date: str) -> str:
    """Last-Modified as a validator, else "": only when the reply's Date is a
    second or more later (RFC 9110 8.8.2.2), as a file rewritten within the
    second it was served in keeps its Last-Modified."""
    modified, sent = _http_date(last_modified), _http_date(date)
    try:
        age = parsedate_to_datetime(sent) - parsedate_to_datetime(modified)
    except (TypeError, ValueError, OverflowError, IndexError):
        return ""
    return modified if age.total_seconds() >= 1 else ""


def range_start(content_range: str | None) -> int | None:
    """The first byte a 206's ``Content-Range`` says it carries."""
    match = _CONTENT_RANGE.fullmatch((content_range or "").strip())
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class Revision:
    """One version of a remote file, as its response headers name it.

    Bytes appended to a partial download must come from the version that wrote
    it (#1225): ``validator`` (a strong ETag, else a Last-Modified at least a
    second before the reply's Date) goes in If-Range, so a server whose file
    changed sends the whole new file, and a reply naming another version is
    refused. Without a validator a partial can't be tied to a version, and
    resuming it is not safe. A reply's weak ETag and any Last-Modified it sent
    still show a change (``shown_*``), though they can't vouch for a match.
    """

    size: int | None = None
    etag: str = ""
    last_modified: str = ""
    shown_etag: str = field(default="", compare=False, repr=False)
    shown_last_modified: str = field(default="", compare=False, repr=False)

    @classmethod
    def from_headers(cls, headers: Message) -> Revision:
        match = _CONTENT_RANGE.fullmatch((headers.get("Content-Range") or "").strip())
        length = (headers.get("Content-Length") or "").strip()
        if match:
            size = int(match.group(3)) if match.group(3) != "*" else None
        else:
            size = int(length) if _DIGITS.fullmatch(length) else None
        etag = (headers.get("ETag") or "").strip()
        last_modified = (headers.get("Last-Modified") or "").strip()
        return cls(
            size,
            _strong_etag(etag),
            _strong_date(last_modified, headers.get("Date") or ""),
            etag,
            last_modified,
        )

    @classmethod
    def from_json(cls, record: object) -> Revision | None:
        if not isinstance(record, dict):
            return None
        size, etag = record.get("size"), record.get("etag", "")
        last_modified = record.get("last_modified", "")
        if not (size is None or (type(size) is int and size >= 0)):
            return None
        # Only what from_headers keeps: these are sent back in If-Range.
        if not isinstance(etag, str) or _strong_etag(etag) != etag:
            return None
        if not isinstance(last_modified, str) or _http_date(last_modified) != (
            last_modified
        ):
            return None
        return cls(size, etag, last_modified)

    def to_json(self) -> dict[str, int | str | None]:
        return {
            "size": self.size,
            "etag": self.etag,
            "last_modified": self.last_modified,
        }

    @property
    def validator(self) -> str:
        return self.etag or self.last_modified

    def same_as(self, other: Revision) -> bool:
        """``other`` names this version by the same validator and size."""
        return bool(self.validator) and (self.validator, self.size) == (
            other.validator,
            other.size,
        )

    def conflicts(self, other: Revision) -> bool:
        """A reply's headers name another version than this one.

        A 206 that leaves a validator out was matched by If-Range."""
        if None not in (self.size, other.size) and self.size != other.size:
            return True
        etag = other.shown_etag or other.etag
        if self.etag and etag:
            return self.etag != etag
        last_modified = other.shown_last_modified or other.last_modified
        if self.last_modified and last_modified:
            return self.last_modified != last_modified
        return False


def ranged_revision(
    url: str, cancel_event: CancelEvent | None = None
) -> Revision | None:
    """The file's size and version, if its server says it takes byte ranges."""
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": user_agent(url)})
        response_cm = (
            cancellable_http(request, TIMEOUT_SECONDS, cancel_event)
            if cancel_event is not None
            else urlopen(request, timeout=TIMEOUT_SECONDS)
        )
        with response_cm as response:
            headers = response.headers
    except (DownloadCancelled, OSError, HTTPException):
        return None
    length = headers.get("Content-Length") or ""
    if headers.get("Accept-Ranges", "").lower() != "bytes" or not length.isdigit():
        return None
    return Revision.from_headers(headers)


def _load_state(
    path: str, url_digest: str, expected: Revision | None = None
) -> tuple[Revision, dict[str, int]] | None:
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(state, dict) or state.get("url_sha256") != url_digest:
        return None
    revision = Revision.from_json(state)
    # State from another version, or from none that can be told apart.
    if revision is None or not revision.validator:
        return None
    if expected is not None and not revision.same_as(expected):
        return None
    size = revision.size
    if size is None or size <= 0:
        return None
    received = state.get("received")
    if (
        not isinstance(received, dict)
        or len(received) != (size - 1) // SEGMENT_SIZE + 1
    ):
        return None
    counts = {}
    for start in range(0, size, SEGMENT_SIZE):
        key = str(start)
        count = received.get(key)
        if type(count) is not int or not 0 <= count <= min(SEGMENT_SIZE, size - start):
            return None
        counts[key] = count
    return revision, counts


def saved_revision(state_path: str, url: str) -> Revision | None:
    """The version an unfinished segmented download of url recorded, if any."""
    state = _load_state(state_path, hashlib.sha256(url.encode()).hexdigest())
    return state[0] if state is not None else None


def download_segmented(
    url: str,
    dest: str,
    remote: Revision,
    progress_callback: ProgressCallback | None = None,
    cancel_event: CancelEvent | None = None,
    *,
    verify: Callable[[str], bool] | None = None,
) -> bool:
    """Fetch remote (a version with a size and validator) into dest.partial,
    then verify and atomically replace dest.

    Interruptions retain partial/state; failed verification discards them.
    Raises RangesUnsupported if the server ignores byte ranges after all, and
    RemoteChanged if a piece comes from another version of the file.
    """
    if remote.size is None or not remote.validator:
        raise ValueError("a segmented download needs the file's size and validator")
    size = remote.size
    partial = dest + ".partial"
    state_path = partial + ".segments"
    url_digest = hashlib.sha256(url.encode()).hexdigest()
    location = redact_url(url)
    starts = list(range(0, size, SEGMENT_SIZE))
    try:
        partial_size = Path(partial).stat().st_size
    except FileNotFoundError:
        partial_size = None
    state = (
        _load_state(state_path, url_digest, remote) if partial_size == size else None
    )
    if state is None:
        # A sparse segmented partial must never look like a single download.
        Path(partial + ".source").unlink(missing_ok=True)
        Path(state_path).unlink(missing_ok=True)
        with open(partial, "wb") as f:
            f.truncate(size)
        received = {str(start): 0 for start in starts}
    else:
        _, received = state
    lock = threading.Lock()
    stop = threading.Event()

    def save_state() -> None:
        with lock:
            snapshot = dict(received)
        tmp = state_path + ".tmp"
        with open(tmp, "w", newline="\n") as f:
            json.dump(
                {"url_sha256": url_digest, **remote.to_json(), "received": snapshot},
                f,
            )
        Path(tmp).replace(state_path)

    def fetch(start: int) -> None:
        end = min(start + SEGMENT_SIZE, size)
        for attempt in range(1, ATTEMPTS + 1):
            if stop.is_set() or (cancel_event and cancel_event.is_set()):
                return
            pos = start + received[str(start)]
            if pos >= end:
                return
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": user_agent(url),
                        "Range": f"bytes={pos}-{end - 1}",
                        # A server whose file changed sends all of it (200).
                        "If-Range": remote.validator,
                    },
                )
                response_cm = (
                    cancellable_http(request, TIMEOUT_SECONDS, cancel_event)
                    if cancel_event is not None
                    else urlopen(request, timeout=TIMEOUT_SECONDS)
                )
                with response_cm as response:
                    if remote.conflicts(Revision.from_headers(response.headers)):
                        raise RemoteChanged(location)
                    if response.getcode() != 206:
                        raise RangesUnsupported(location)
                    if range_start(response.headers.get("Content-Range")) != pos:
                        raise RemoteChanged(location)
                    with open(partial, "r+b") as f:
                        f.seek(pos)
                        while pos < end:
                            if stop.is_set():
                                return
                            chunk = response.read(min(_CHUNK, end - pos))
                            if not chunk:
                                raise DownloadIncomplete(
                                    f"bytes {pos}-{end - 1}: connection closed"
                                )
                            f.write(chunk)
                            # The state may be saved, then the process killed,
                            # before close: count only what reached the file.
                            f.flush()
                            pos += len(chunk)
                            with lock:
                                received[str(start)] = pos - start
                return
            except (HTTPException, OSError, ValueError) as e:
                if cancel_event and cancel_event.is_set():
                    return
                if isinstance(e, HTTPError):
                    e.close()
                    detail = f"HTTP {e.code}"
                else:
                    detail = redact_urls(str(e), sources=(url,))
                if attempt == ATTEMPTS or not _transient(e):
                    raise OSError(f"{location}: {detail}") from None
                logging.debug(
                    "Retrying %s bytes %s-%s: %s", location, pos, end - 1, detail
                )
                # Cancelled, or another piece failed: stop backing off.
                if stop.wait(_backoff(attempt)):
                    return

    label = Path(dest).name
    report = progress_callback or PercentProgressLogger(label)
    logging.info(f"Downloading {label} over {CONNECTIONS} connections...")
    start_time = time.monotonic()
    already = sum(received.values())
    with ContextThreadPoolExecutor(CONNECTIONS) as pool:
        futures: set[Future[None]] = {
            pool.submit(fetch, start)
            for start in starts
            if received[str(start)] < min(SEGMENT_SIZE, size - start)
        }
        try:
            pending = futures
            while pending:
                done, pending = wait(pending, timeout=2, return_when=FIRST_EXCEPTION)
                for future in done:
                    future.result()  # raises the piece's error
                if cancel_event and cancel_event.is_set():
                    raise DownloadCancelled("Download cancelled by user.")
                with lock:
                    total = sum(received.values())
                elapsed = time.monotonic() - start_time
                report(total, size, (total - already) / elapsed if elapsed else 0.0)
                if pending:
                    save_state()
        except BaseException:
            stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            try:
                save_state()
            except OSError as error:
                logging.warning(
                    "Could not save download progress for %s: %s", label, error
                )
            raise
    save_state()

    if sum(received.values()) != size:
        raise DownloadIncomplete(f"{label}: {sum(received.values())} of {size} bytes")
    if verify is not None and not verify(partial):
        Path(partial).unlink(missing_ok=True)
        Path(state_path).unlink(missing_ok=True)
        return False
    Path(partial).replace(dest)
    Path(state_path).unlink()
    return True
