"""Read a snapshot of a worker log without buffering the entire response."""

from __future__ import annotations

import codecs
import io
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

_BLOCK_BYTES = 64 * 1024
_NEWLINES = re.compile(rb"\r\n?|\n")


def _tail_start(log: BinaryIO, size: int, tail: int) -> int:
    end = size
    next_starts_with_lf = False
    while end:
        start = max(0, end - _BLOCK_BYTES)
        log.seek(start)
        block = log.read(end - start)
        # A CRLF split between blocks was already counted in the later block.
        scan = block[:-1] if next_starts_with_lf and block.endswith(b"\r") else block
        for newline in reversed(list(_NEWLINES.finditer(scan))):
            offset = start + newline.end()
            if offset < size:
                tail -= 1
                if not tail:
                    return offset
        next_starts_with_lf = block.startswith(b"\n")
        end = start
    return 0


def _chunks(log: BinaryIO, remaining: int) -> Iterator[bytes]:
    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True
    )
    ended_line = False
    while remaining and (block := log.read(min(remaining, _BLOCK_BYTES))):
        remaining -= len(block)
        text = decoder.decode(block)
        if text:
            yield text.encode()
            ended_line = text.endswith("\n")
    last = decoder.decode(b"", final=True)
    if last:
        yield last.encode()
        ended_line = last.endswith("\n")
    if not ended_line:
        yield b"\n"


@contextmanager
def read_job_log(path: Path, tail: int) -> Iterator[Iterator[bytes]]:
    """Yield UTF-8 log chunks, starting at the last ``tail`` lines (0: all)."""
    log: BinaryIO
    try:
        log = path.open("rb")
    except FileNotFoundError:
        log = io.BytesIO()
    with log:
        size = log.seek(0, io.SEEK_END)
        start = _tail_start(log, size, tail) if tail else 0
        log.seek(start)
        yield _chunks(log, size - start)
