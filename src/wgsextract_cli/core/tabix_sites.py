"""The records of a tabix-indexed, bgzipped VCF at a list of sites, read with
HTTP byte ranges (or from a local file) instead of whole (issue 629).

A tabix index only places records to 16 kb windows, which in gnomAD v4.1 is
about 5 MB of compressed records: reading from the window's start, as tabix
does, would read most of a 15 GB chromosome for a genome's sites. So each site
is found within its window by interpolation on the compressed offset: a probe
finds the next BGZF block from any byte, and the position of the first whole
record in that block says which way to go. Records sorted by position are read
from a block whose first whole record lies before the site until one lies past
it. Every block's CRC-32 is checked, and the file must not change during the
run (``If-Range``): a changed file is an error, never a mix of two releases.
"""

from __future__ import annotations

import bisect
import gzip
import http.client
import logging
import os
import struct
import threading
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from functools import cached_property
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, getproxies, proxy_bypass, urlopen

from wgsextract_cli.core import read_scope
from wgsextract_cli.core._tabix_sweep import sweep_local
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.network_policy import require_network
from wgsextract_cli.core.ref_library.segmented import user_agent
from wgsextract_cli.core.utils import WGSExtractError

_MIN_SHIFT = 14  # tabix's linear index: 16 kb windows
_LEVELS = ((26, 1), (23, 9), (20, 73), (17, 585), (14, 4681))
_META_BIN = 37450  # htslib's pseudo-bin of index metadata
_MAGIC = b"\x1f\x8b\x08\x04"
_BC = b"\x06\x00BC\x02\x00"  # XLEN 6, the BC subfield of length 2
_MAX_BLOCK = 65536
# Sites in one task, read in order by one connection: near ones share blocks.
_GROUP = 128
_CACHED = 64  # blocks a search keeps
# Blocks read on rather than probed for: a request costs far more time than a
# block of bytes.
_READ_ON = 4
_ATTEMPTS = 6
_SWEEP_MIN_SITES = 128
# Roughly eight targets per 16 Ki-base index window on average.
_SWEEP_MEAN_GAP = 2048


# Google Cloud Storage serves many connections from one address; Ensembl's FTP
# site slows past about 16 and refuses connections at 64.
_MANY_CONNECTIONS = ("storage.googleapis.com",)
_FEW_CONNECTIONS = 16


def connections(location: str, wanted: int) -> int:
    """Connections to read ``location`` with: ``wanted``, fewer for a server
    not known to take many."""
    host = urlsplit(location).hostname or ""
    if not location.startswith(("http://", "https://")) or host in _MANY_CONNECTIONS:
        return wanted
    return min(wanted, _FEW_CONNECTIONS)


class SourceChanged(WGSExtractError):
    """The remote file changed during the run, or ignores byte ranges."""


@dataclass
class _Contig:
    bins: dict[int, list[tuple[int, int]]]
    linear: list[int]

    @cached_property
    def start(self) -> int:
        """The virtual offset of the contig's first record."""
        return min(beg for chunks in self.bins.values() for beg, _ in chunks)

    @cached_property
    def end(self) -> int:
        """The virtual offset just past the contig's last record."""
        return max(end for chunks in self.bins.values() for _, end in chunks)


@dataclass
class TabixIndex:
    contigs: dict[str, _Contig]
    meta: str


def parse_tbi(raw: bytes) -> TabixIndex:
    """A ``.tbi`` (bgzipped or not) as its bins and linear index per contig."""
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    if data[:4] != b"TBI\x01":
        raise WGSExtractError("not a tabix (.tbi) index")
    offset = 4
    n_ref, _fmt, _seq, _beg, _end, meta, _skip, l_nm = struct.unpack_from(
        "<8i", data, offset
    )
    offset += 32
    names = data[offset : offset + l_nm].split(b"\0")[:n_ref]
    offset += l_nm
    contigs = {}
    for name in names:
        (n_bin,) = struct.unpack_from("<i", data, offset)
        offset += 4
        bins: dict[int, list[tuple[int, int]]] = {}
        for _ in range(n_bin):
            bin_id, n_chunk = struct.unpack_from("<Ii", data, offset)
            offset += 8
            flat = struct.unpack_from(f"<{2 * n_chunk}Q", data, offset)
            offset += 16 * n_chunk
            if bin_id != _META_BIN:
                bins[bin_id] = list(zip(flat[::2], flat[1::2], strict=True))
        (n_intv,) = struct.unpack_from("<i", data, offset)
        offset += 4
        linear = list(struct.unpack_from(f"<{n_intv}Q", data, offset))
        offset += 8 * n_intv
        if bins:
            contigs[name.decode()] = _Contig(bins, linear)
    return TabixIndex(contigs, chr(meta))


class ByteSource:
    """Byte ranges of a URL (one keep-alive connection per thread) or of a
    local file, counting what was read."""

    def __init__(self, location: str, cancel: CancelEvent | None = None):
        self.location = location
        self.cancel = cancel
        self.remote = location.startswith(("http://", "https://"))
        self.bytes_read = 0
        self.requests = 0
        self._lock = threading.Lock()
        self._local = threading.local()
        self.etag = self.last_modified = ""
        self.local_identity: tuple[int, int, int, int, int] | None = None
        if self.remote:
            require_network("Remote annotation byte ranges")
            self.url, self.size, headers = self._head(location)
            self.etag = headers.get("ETag") or ""
            self.last_modified = headers.get("Last-Modified") or ""
            if not self.validator:
                raise WGSExtractError(
                    f"{location} has no strong ETag or Last-Modified validator; "
                    "refusing unversioned range reads."
                )
        else:
            self.url = location
            self._check_scope()
            # Windows stat(path) and fstat(fd) can report different device IDs
            # and ctimes. Use descriptor metadata for every identity check.
            with open(location, "rb") as f:
                self.local_identity = self._stat_identity(os.fstat(f.fileno()))
            self.size = self.local_identity[2]

    @cached_property
    def validator(self) -> str:
        if self.etag.startswith('"') and self.etag.endswith('"'):
            return self.etag
        if self.last_modified:
            try:
                parsedate_to_datetime(self.last_modified)
            except (TypeError, ValueError, OverflowError):
                return ""
            return self.last_modified
        return ""

    def _check_scope(self) -> None:
        if not read_scope.may_follow(self.location):
            raise WGSExtractError(
                f"{self.location} is outside the folders this run may use."
            )

    @staticmethod
    def _stat_identity(stat: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    def _unchanged(self, stat: os.stat_result) -> None:
        if self._stat_identity(stat) != self.local_identity:
            raise SourceChanged(f"{self.location} changed during the run.")

    def verify(self) -> None:
        """Refuse a local source replaced or modified since it was opened."""
        if not self.remote:
            self._check_scope()
            with open(self.location, "rb") as f:
                self._unchanged(os.fstat(f.fileno()))

    @contextmanager
    def local_stream(self, offset: int) -> Iterator[BinaryIO]:
        """Pin the checked input descriptor for a local child reader."""
        assert not self.remote
        if offset >= self.size:
            raise WGSExtractError(f"{self.location}: tabix offset past end of file.")
        self._check_scope()
        with open(self.location, "rb", buffering=0) as stream:
            self._unchanged(os.fstat(stream.fileno()))
            stream.seek(offset)
            yield stream
            self._unchanged(os.fstat(stream.fileno()))
            size = stream.tell() - offset
        self.verify()
        with self._lock:
            self.bytes_read += size
            self.requests += 1

    def _head(self, url: str) -> tuple[str, int, http.client.HTTPMessage]:
        request = Request(url, method="HEAD", headers={"User-Agent": user_agent(url)})
        for attempt in range(_ATTEMPTS):
            try:
                with urlopen(request, timeout=60) as response:
                    length = response.headers.get("Content-Length") or ""
                    if not length.isdigit():
                        raise WGSExtractError(f"{url} gave no size.")
                    return response.url, int(length), response.headers
            except HTTPError as e:
                if e.code == 404:
                    raise FileNotFoundError(url) from e
                if e.code != 429 and e.code < 500:
                    raise WGSExtractError(f"Reading {url} failed: {e}") from e
                self._wait(url, attempt, e)
            except (URLError, OSError, http.client.HTTPException) as e:
                self._wait(url, attempt, e)
        raise AssertionError("unreachable")

    def _wait(self, url: str, attempt: int, error: Exception) -> None:
        if attempt + 1 >= _ATTEMPTS:
            raise WGSExtractError(f"Reading {url} failed: {error}") from error
        if self.cancel and self.cancel.is_set():
            raise WGSExtractError("Cancelled.")
        logging.debug("Reading %s: %s; retrying.", url, error)
        time.sleep(0.5 * 2**attempt)

    def _connection(self) -> http.client.HTTPConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            parts = urlsplit(self.url)
            kind = (
                http.client.HTTPSConnection
                if parts.scheme == "https"
                else http.client.HTTPConnection
            )
            conn = kind(parts.netloc, timeout=120)
            self._local.conn = conn
        return conn

    def _drop_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def read(self, start: int, end: int) -> bytes:
        """Bytes ``[start, end)``, clipped to the file."""
        end = min(end, self.size)
        if end <= start:
            return b""
        if self.cancel and self.cancel.is_set():
            raise WGSExtractError("Cancelled.")
        if not self.remote:
            self._check_scope()
            with open(self.location, "rb") as f:
                self._unchanged(os.fstat(f.fileno()))
                f.seek(start)
                data = f.read(end - start)
                self._unchanged(os.fstat(f.fileno()))
            self.verify()
        else:
            data = self._get(start, end)
        with self._lock:
            self.bytes_read += len(data)
            self.requests += 1
        return data

    def _get(self, start: int, end: int) -> bytes:
        parts = urlsplit(self.url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {
            "User-Agent": user_agent(self.url),
            "Range": f"bytes={start}-{end - 1}",
        }
        # A strong ETag, else the date: a server whose file changed answers
        # with the whole new file (200) instead of the range.
        headers["If-Range"] = self.validator
        if getproxies().get(parts.scheme) and not proxy_bypass(parts.hostname or ""):
            return self._get_proxied(start, end, headers)
        data = b""
        for attempt in range(_ATTEMPTS):
            conn = self._connection()
            try:
                conn.request("GET", target, headers=headers)
                response = conn.getresponse()
                if response.status == 206:
                    if (
                        response.headers.get("Content-Range")
                        != f"bytes {start}-{end - 1}/{self.size}"
                    ):
                        raise SourceChanged(
                            f"{self.url} returned an inconsistent byte range."
                        )
                    data = bytes(response.read(end - start + 1))
                    if len(data) == end - start:
                        if response.will_close:
                            self._drop_connection()
                        return data
                    raise http.client.IncompleteRead(data, end - start - len(data))
                self._drop_connection()
                if response.status in (200, 416):
                    raise SourceChanged(
                        f"{self.url} changed during the run, or doesn't serve byte "
                        f"ranges (HTTP {response.status}); run again to read it whole."
                    )
                if response.status != 429 and response.status < 500:
                    raise WGSExtractError(
                        f"Reading {self.url} failed: HTTP {response.status}"
                    )
                self._wait(self.url, attempt, OSError(f"HTTP {response.status}"))
            except (OSError, http.client.HTTPException) as e:
                self._drop_connection()
                self._wait(self.url, attempt, e)
        raise AssertionError("unreachable")

    def _get_proxied(self, start: int, end: int, headers: dict[str, str]) -> bytes:
        for attempt in range(_ATTEMPTS):
            try:
                with urlopen(
                    Request(self.url, headers=headers), timeout=120
                ) as response:
                    if (
                        response.status != 206
                        or response.headers.get("Content-Range")
                        != f"bytes {start}-{end - 1}/{self.size}"
                    ):
                        raise SourceChanged(
                            f"{self.url} changed during the run, or ignores byte ranges."
                        )
                    data = bytes(response.read(end - start + 1))
                    if len(data) != end - start:
                        raise http.client.IncompleteRead(data, end - start - len(data))
                    return data
            except HTTPError as e:
                if e.code in (200, 416):
                    raise SourceChanged(f"{self.url} changed during the run.") from e
                if e.code != 429 and e.code < 500:
                    raise WGSExtractError(f"Reading {self.url} failed: {e}") from e
                self._wait(self.url, attempt, e)
            except (URLError, OSError, http.client.HTTPException) as e:
                self._wait(self.url, attempt, e)
        raise AssertionError("unreachable")


@dataclass
class _Block:
    start: int  # compressed offset
    size: int
    data: bytes


class _Blocks:
    """BGZF blocks of one file, with the size seen so far to size reads."""

    def __init__(self, source: ByteSource, typical: int = 16384):
        self.source = source
        self.typical = typical
        self.cache: dict[int, _Block] = {}
        # Every block seen: its start, and the contig, position and offset
        # of its first whole record, sorted by start.
        self.starts: list[int] = []
        self.firsts: list[tuple[bytes, int, int]] = []

    def _parse(self, buf: bytes, at: int, offset: int) -> _Block | None:
        """The block starting at ``buf[at]``, if one does, is whole and its
        CRC-32 holds."""
        head = buf[at : at + 18]
        if len(head) < 18 or head[:4] != _MAGIC or head[10:16] != _BC:
            return None
        size = struct.unpack_from("<H", head, 16)[0] + 1
        if at + size > len(buf):
            return None
        crc, isize = struct.unpack_from("<II", buf, at + size - 8)
        try:
            data = zlib.decompress(buf[at + 18 : at + size - 8], -15)
        except zlib.error:
            return None
        if len(data) != isize or zlib.crc32(data) != crc:
            return None
        self.typical = max(1024, (self.typical * 7 + size) // 8)
        return _Block(offset + at, size, data)

    def _keep(self, buf: bytes, at: int, offset: int) -> _Block | None:
        """Cache the whole blocks chained from ``buf[at]``, as the most
        recently used; returns the first, if there is one."""
        first = None
        while (block := self._parse(buf, at, offset)) is not None:
            self.cache.pop(block.start, None)
            self.cache[block.start] = block
            self._note(block)
            first = first or block
            at += block.size
        while len(self.cache) > _CACHED:
            self.cache.pop(next(iter(self.cache)))
        return first

    def _note(self, block: _Block) -> None:
        first = _first_record(block.data, 0)
        if first is None:
            return
        where = bisect.bisect_left(self.starts, block.start)
        if where < len(self.starts) and self.starts[where] == block.start:
            return
        line = block.data[first:].split(b"\n", 1)[0]
        if line.startswith(b"#") or line.count(b"\t") < 2:
            return
        record = _record(line)
        self.starts.insert(where, block.start)
        self.firsts.insert(where, (record.chrom, record.pos, first))

    def at(self, start: int, ahead: int = 1) -> _Block:
        """The block that starts at ``start``, reading ``ahead`` blocks'
        worth so that the next ones are cached too."""
        block = self.cache.get(start)
        if block is not None:
            return block
        buf = self.source.read(start, start + ahead * self.typical + 1024)
        if len(buf) >= 18 and buf[:4] == _MAGIC:
            size = struct.unpack_from("<H", buf, 16)[0] + 1
            if size > len(buf):
                buf += self.source.read(start + len(buf), start + size)
        block = self._keep(buf, 0, start)
        if block is None:
            raise WGSExtractError(
                f"{self.source.url} has no valid BGZF block at byte {start}: "
                "the index doesn't match the file, or a block is damaged."
            )
        return block

    def after(self, offset: int, limit: int) -> _Block | None:
        """The first whole block starting at or after ``offset`` and before
        ``limit``, found by its header and checked by its CRC-32."""
        buf, scan = b"", 0
        while offset + scan < limit:
            if len(buf) < scan + 18 + self.typical:
                more = self.source.read(
                    offset + len(buf), offset + scan + 18 + 2 * self.typical
                )
                if not more:
                    return None
                buf += more
            at = buf.find(_MAGIC, scan)
            if at == -1 or len(buf) < at + 18:
                scan = max(scan, len(buf) - 17) if at == -1 else at
                continue
            if offset + at >= limit:
                return None
            if buf[at + 10 : at + 16] == _BC:
                size = struct.unpack_from("<H", buf, at + 16)[0] + 1
                if at + size > len(buf):
                    buf += self.source.read(offset + len(buf), offset + at + size)
                if (block := self._keep(buf, at, offset)) is not None:
                    return block
            scan = at + 1
        return None


@dataclass
class _Record:
    chrom: bytes
    pos: int
    line: bytes


def _first_record(data: bytes, start: int) -> int | None:
    """Offset of the first line that starts after ``start`` in ``data``
    and ends in it."""
    newline = data.find(b"\n", start)
    if newline == -1 or data.find(b"\n", newline + 1) == -1:
        return None
    return newline + 1


def _record(line: bytes) -> _Record:
    chrom, pos, _ = line.split(b"\t", 2)
    return _Record(chrom, int(pos), line)


@dataclass
class _Reader:
    """Reads records in file order from a record boundary onwards."""

    blocks: _Blocks
    contig: bytes
    end: int  # compressed offset past the contig's records
    block: _Block
    text: bytes = b""
    offset: int = 0
    done: bool = False

    @classmethod
    def at(
        cls, blocks: _Blocks, contig: bytes, end: int, block: _Block, at: int
    ) -> _Reader:
        return cls(blocks, contig, end, block, block.data, at)

    def records(self) -> Iterator[_Record]:
        while not self.done:
            newline = self.text.find(b"\n", self.offset)
            if newline == -1:
                nxt = self.block.start + self.block.size
                if nxt >= self.end or nxt >= self.blocks.source.size:
                    self.done = True
                    break
                self.block = self.blocks.at(nxt, ahead=_READ_ON)
                self.text = self.text[self.offset :] + self.block.data
                self.offset = 0
                continue
            line = self.text[self.offset : newline]
            self.offset = newline + 1
            if not line or line.startswith(b"#"):
                continue
            record = _record(line)
            if record.chrom != self.contig:
                self.done = True
                break
            yield record


@dataclass
class _Search:
    """Finds, then reads, the records at a contig's sites, in order."""

    blocks: _Blocks
    name: bytes
    contig: _Contig
    reader: _Reader | None = None
    pending: _Record | None = None
    found: list[bytes] = field(default_factory=list)

    def _bounds(self, pos: int) -> tuple[int, int, int, int]:
        """Around ``pos`` (1-based): the virtual offset from which reading on
        reaches every record at or after it (tabix's linear index entry), its
        window's first position, and a compressed offset and position further
        on to interpolate towards (the next window's start, or the end)."""
        linear = self.contig.linear
        window = min((pos - 1) >> _MIN_SHIFT, len(linear) - 1)
        start = max(linear[window] if window >= 0 else 0, self.contig.start)
        for later in range(window + 1, len(linear)):
            if (linear[later] >> 16) > (start >> 16):
                return (
                    start,
                    (window << _MIN_SHIFT) + 1,
                    linear[later] >> 16,
                    (later << _MIN_SHIFT) + 1,
                )
        end = (self.contig.end >> 16) + 1
        return start, (window << _MIN_SHIFT) + 1, end, (len(linear) << _MIN_SHIFT) + 1

    def _density(self, pos: int) -> float:
        """Compressed bytes a base around ``pos``."""
        start, start_pos, high, high_pos = self._bounds(pos)
        return ((high - (start >> 16)) / max(1, high_pos - start_pos)) or 1.0

    def _seek(self, lo: int) -> None:
        """Put the reader before the first record at or after ``lo``."""
        start, low_pos, high, high_pos = self._bounds(lo)
        low = start >> 16
        best = (low, start & 0xFFFF)
        end = (self.contig.end >> 16) + _MAX_BLOCK
        blocks = self.blocks
        for _ in range(24):
            # The closest blocks seen on either side of the site.
            for where in range(
                bisect.bisect_right(blocks.starts, low), len(blocks.starts)
            ):
                block_start = blocks.starts[where]
                if block_start >= high:
                    break
                chrom, pos, first = blocks.firsts[where]
                if chrom == self.name and pos < lo:
                    low, low_pos, best = block_start, pos, (block_start, first)
                else:
                    high, high_pos = (
                        block_start,
                        pos if chrom == self.name else high_pos,
                    )
                    break
            typical = blocks.typical
            if high - low <= 3 * typical:
                break
            density = (high - low) / max(1, high_pos - low_pos)
            if (lo - low_pos) * density < _READ_ON * typical:
                break  # reading on from low is as cheap as another probe
            # Aim a block early: landing before the site costs a block read,
            # landing past it another probe.
            guess = low + int((lo - low_pos) * density) - typical
            guess = max(low + typical, min(high - 2 * typical, guess))
            found = blocks.after(guess, min(high, end))
            if found is None:
                high = guess
            elif found.start not in blocks.starts:
                low = found.start  # no whole record in it: look further on
        self.reader = _Reader.at(
            self.blocks, self.name, end, self.blocks.at(best[0]), best[1]
        )
        self.pending = None

    def _next(self) -> _Record | None:
        if self.pending is None and self.reader is not None:
            self.pending = next(self.reader.records(), None)
        return self.pending

    def collect(self, lo: int, hi: int) -> None:
        """Every record with ``lo <= POS <= hi``, after those of the sites
        before."""
        record = self._next()
        if self.reader is None or (
            record is not None
            and record.pos < lo
            and (lo - record.pos) * self._density(lo) > 3 * self.blocks.typical
        ):
            self._seek(lo)
        while (record := self._next()) is not None and record.pos <= hi:
            if record.pos >= lo:
                self.found.append(record.line)
            self.pending = None


class TabixFile:
    """A bgzipped, tabix-indexed VCF at a URL or path; its index is read
    when first needed."""

    def __init__(self, location: str, cancel: CancelEvent | None = None):
        self.location = location
        self.cancel = cancel
        self.source = ByteSource(location, cancel)
        self._index: TabixIndex | None = None
        self.index_bytes = 0
        self.local_sweeps = 0
        self._header: bytes | None = None
        self._typical = 16384

    @property
    def index(self) -> TabixIndex:
        if self._index is None:
            try:
                index = ByteSource(f"{self.location}.tbi", self.cancel)
            except FileNotFoundError as e:
                raise WGSExtractError(
                    f"{self.location} has no tabix index ({self.location}.tbi)."
                ) from e
            raw = index.read(0, index.size)
            self.index_bytes = len(raw)
            self._index = parse_tbi(raw)
        return self._index

    @property
    def bytes_read(self) -> int:
        return self.source.bytes_read + self.index_bytes

    def header(self) -> bytes:
        """The header lines, read block by block from the start."""
        if self._header is None:
            blocks = _Blocks(self.source)
            text, start = b"", 0
            while True:
                block = blocks.at(start)
                text += block.data
                at = 0
                while (newline := text.find(b"\n", at)) != -1:
                    if not text.startswith(b"#", at):
                        self._header = text[:at]
                        return self._header
                    at = newline + 1
                start += block.size
                if start >= self.source.size or not block.data:
                    self._header = text
                    return text
        return self._header

    def first_record(self, contig: str) -> bytes | None:
        """The contig's first record."""
        found = self.index.contigs.get(contig)
        if found is None:
            return None
        blocks = _Blocks(self.source)
        start = found.start
        reader = _Reader.at(
            blocks, contig.encode(), (found.end >> 16) + _MAX_BLOCK,
            blocks.at(start >> 16), start & 0xFFFF,
        )  # fmt: skip
        record = next(reader.records(), None)
        return record.line if record else None

    def records_at(
        self,
        contig: str,
        sites: Iterable[tuple[int, int]],
        workers: int = 8,
        on_group: Callable[[list[bytes]], None] | None = None,
        *,
        sweep_dir: Path | None = None,
    ) -> list[bytes]:
        """The contig's records with POS in any ``(first, last)`` site
        (1-based, inclusive, sorted, not overlapping), in file order; with
        ``on_group``, handed to it in order a group at a time instead.
        A private ``sweep_dir`` enables an indexed sweep for dense local sites."""
        found = self.index.contigs.get(contig)
        ordered = list(sites)
        if found is None or not ordered:
            return []
        records: list[bytes] = []
        sink = on_group or records.extend
        if sweep_dir is not None and self._sweep(
            contig, found, ordered, sweep_dir, sink
        ):
            return records
        name = contig.encode()
        # Enough groups to keep every connection busy; near sites in a group
        # share the blocks read.
        size = max(8, min(_GROUP, -(-len(ordered) // (4 * max(1, workers)))))
        groups = [ordered[i : i + size] for i in range(0, len(ordered), size)]

        def run(group: list[tuple[int, int]]) -> list[bytes]:
            search = _Search(_Blocks(self.source, self._typical), name, found)
            for lo, hi in group:
                search.collect(lo, hi)
            self._typical = search.blocks.typical
            return search.found

        pool = ContextThreadPoolExecutor(max_workers=max(1, workers))
        try:
            pending: deque[Future[list[bytes]]] = deque()
            for group in groups:
                pending.append(pool.submit(run, group))
                if len(pending) >= 4 * workers:
                    sink(pending.popleft().result())
            while pending:
                sink(pending.popleft().result())
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return records

    def _sweep(
        self,
        contig: str,
        bounds: _Contig,
        sites: list[tuple[int, int]],
        work: Path,
        sink: Callable[[list[bytes]], None],
    ) -> bool:
        # A sweep pays for the gaps too; the range reader can jump over them.
        if (
            self.source.remote
            or self.index.meta != "#"
            or len(sites) < _SWEEP_MIN_SITES
            or sites[-1][0] - sites[0][1] > _SWEEP_MEAN_GAP * len(sites)
            or not (bgzip := get_tool_path("bgzip"))
        ):
            return False
        linear = bounds.linear
        window = min((sites[0][0] - 1) >> _MIN_SHIFT, len(linear) - 1)
        start = max(linear[window] if window >= 0 else 0, bounds.start)
        with self.source.local_stream(start >> 16) as source:
            sweep_local(
                bgzip, source, start & 0xFFFF, contig, sites, work, sink, self.cancel
            )
        self.local_sweeps += 1
        return True
