"""Repair a checksum-mismatched BGZF partial before it can be published."""

from __future__ import annotations

import hashlib
import logging
import zlib
from collections.abc import Callable
from http.client import HTTPException
from io import BytesIO
from typing import BinaryIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wgsextract_cli.core.cancellable_http import cancellable_http
from wgsextract_cli.core.download_progress import (
    CancelEvent,
    DownloadCancelled,
    redact_urls,
    retryable_http_status,
)

from .segmented import TIMEOUT_SECONDS, Revision, user_agent

_ATTEMPTS = 3
_BLOCK_LIMIT = 65536
_VOTE_WINDOW = 64 << 20


def _block_size(
    read: Callable[[int], bytes], *, require_magic: bool = True
) -> int | None:
    header = read(18)
    if len(header) != 18 or (require_magic and header[:4] != b"\x1f\x8b\x08\x04"):
        return None
    extra_length = int.from_bytes(header[10:12], "little")
    if not 6 <= extra_length <= _BLOCK_LIMIT - 22:
        return None
    extra = header[12:] + (read(extra_length - 6) if extra_length > 6 else b"")
    if len(extra) != extra_length:
        return None
    pos = 0
    while pos + 4 <= extra_length:
        length = int.from_bytes(extra[pos + 2 : pos + 4], "little")
        if pos + 4 + length > extra_length:
            break
        if extra[pos : pos + 2] == b"BC" and length == 2:
            size = int.from_bytes(extra[pos + 4 : pos + 6], "little") + 1
            return size if size >= 22 + extra_length else None
        pos += 4 + length
    return None


def _valid_block(block: bytes) -> bool:
    inflater = zlib.decompressobj(31)
    try:
        # gzip mode verifies both CRC32 and ISIZE on reaching the trailer.
        data = inflater.decompress(block, _BLOCK_LIMIT + 1)
    except zlib.error:
        return False
    return len(data) <= _BLOCK_LIMIT and inflater.eof and not inflater.unused_data


class _RepairError(Exception):
    pass


class _RangeSource:
    def __init__(
        self, url: str, size: int, revision: Revision, cancel: CancelEvent | None
    ) -> None:
        self.url = url
        self.size = size
        self.revision = revision
        self.cancel = cancel

    def check_cancel(self) -> None:
        if self.cancel and self.cancel.is_set():
            raise DownloadCancelled("BGZF repair cancelled by user.")

    def read(self, start: int, length: int) -> bytes:
        self.check_cancel()
        last = start + length - 1
        if last >= self.size:
            raise _RepairError(f"bytes {start}-{last}: beyond end of download")
        headers = {
            "User-Agent": user_agent(self.url),
            "Range": f"bytes={start}-{last}",
            "Accept-Encoding": "identity",
        }
        # As with a pinned single-stream resume, without a validator the
        # whole-file pin is what prevents publishing mixed revisions.
        if self.revision.validator:
            headers["If-Range"] = self.revision.validator
        request = Request(self.url, headers=headers)
        try:
            response_cm = (
                cancellable_http(request, TIMEOUT_SECONDS, self.cancel)
                if self.cancel is not None
                else urlopen(request, timeout=TIMEOUT_SECONDS)
            )
            with response_cm as response:
                if (
                    response.getcode() != 206
                    or response.headers.get("Content-Range", "").strip()
                    != f"bytes {start}-{last}/{self.size}"
                    or self.revision.conflicts(Revision.from_headers(response.headers))
                ):
                    raise _RepairError(
                        f"bytes {start}-{last}: server changed the file or did not "
                        "return the requested byte range"
                    )
                data = response.read(length + 1)
                if len(data) != length:
                    raise OSError(f"bytes {start}-{last}: unexpected response length")
                return data
        except HTTPError as exc:
            exc.close()
            if not retryable_http_status(exc.code):
                raise _RepairError(f"bytes {start}-{last}: HTTP {exc.code}") from exc
            raise

    def repair_block(self, start: int, size: int | None) -> bytes:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                if size is not None and start + size > self.size:
                    size = None
                if size is None:
                    # The member offset is known even if its header was corrupted.
                    pos = start

                    def read_header(length: int) -> bytes:
                        nonlocal pos
                        data = self.read(pos, length)
                        pos += length
                        return data

                    size = _block_size(read_header)
                if size is not None and start + size <= self.size:
                    logging.info(
                        "Re-fetching BGZF bytes %s-%s (attempt %s/%s).",
                        start,
                        start + size - 1,
                        attempt,
                        _ATTEMPTS,
                    )
                    data = self.read(start, size)
                    actual_size = _block_size(BytesIO(data).read)
                    if actual_size == size and _valid_block(data):
                        return data
                    size = actual_size or size
            except (OSError, HTTPException) as exc:
                logging.debug(
                    "BGZF range request failed: %s",
                    redact_urls(str(exc), sources=(self.url,)),
                )
        last = min(start + (size or 18), self.size) - 1
        raise _RepairError(
            f"bytes {start}-{last} still invalid after {_ATTEMPTS} attempts"
        )

    def vote(self, file: BinaryIO) -> None:
        """Use agreement between independent downloads if CRC checks found no damage."""
        for start in range(0, self.size, _VOTE_WINDOW):
            self.check_cancel()
            length = min(_VOTE_WINDOW, self.size - start)
            file.seek(start)
            local = hashlib.sha256(file.read(length)).digest()
            votes = {local}
            for _ in range(_ATTEMPTS):
                try:
                    data = self.read(start, length)
                except (OSError, HTTPException) as exc:
                    logging.debug(
                        "BGZF range request failed: %s",
                        redact_urls(str(exc), sources=(self.url,)),
                    )
                    continue
                digest = hashlib.sha256(data).digest()
                if digest in votes:
                    if digest != local:
                        file.seek(start)
                        file.write(data)
                    break
                votes.add(digest)
            else:
                raise _RepairError(
                    f"bytes {start}-{start + length - 1}: no two copies agreed "
                    f"after {_ATTEMPTS} attempts"
                )


def _matches(file: BinaryIO, expected: str, source: _RangeSource) -> bool:
    file.seek(0)
    digest = hashlib.sha256()
    for data in iter(lambda: file.read(1 << 20), b""):
        source.check_cancel()
        digest.update(data)
    return digest.hexdigest() == expected


def repair_bgzf(
    path: str, url: str, revision: Revision, expected: str, cancel: CancelEvent | None
) -> bool:
    """Repair only the bound staging file, requiring the full pin after changes."""
    location = redact_urls(url)
    try:
        with open(path, "r+b") as file:
            # The BGZF-specific BC field may survive a torn opening gzip magic.
            if _block_size(file.read, require_magic=False) is None:
                return False
            file_size = file.seek(0, 2)
            if revision.size is not None and revision.size != file_size:
                return False
            source = _RangeSource(url, file_size, revision, cancel)
            logging.info("Checking BGZF blocks after checksum mismatch: %s", location)
            start = 0
            repaired = False
            while start < file_size:
                source.check_cancel()
                file.seek(start)
                size = _block_size(file.read)
                file.seek(start)
                if size is not None and start + size > file_size:
                    size = None
                if size is None or not _valid_block(file.read(size)):
                    block = source.repair_block(start, size)
                    file.seek(start)
                    file.write(block)
                    size = len(block)
                    repaired = True
                start += size
            if repaired and _matches(file, expected, source):
                return True
            logging.info(
                "BGZF block checks did not recover the pin; comparing byte ranges: %s",
                location,
            )
            source.vote(file)
            if _matches(file, expected, source):
                return True
            raise _RepairError(
                f"bytes 0-{file_size - 1}: agreed ranges still do not match the pin"
            )
    except (_RepairError, OSError) as exc:
        logging.error(
            "BGZF repair failed for %s: %s. Retry the download or use a verified copy.",
            location,
            redact_urls(str(exc), sources=(url,)),
        )
        return False
