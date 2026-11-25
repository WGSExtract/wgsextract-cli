"""Bounded, byte-exact FASTQ archive framing and input validation."""

from __future__ import annotations

import gzip
import hashlib
import struct
import zlib
from contextlib import ExitStack, contextmanager
from itertools import zip_longest
from typing import TYPE_CHECKING, BinaryIO, Protocol

from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

CHUNK = struct.Struct("<II")
MAX_LINE = 8 << 20
MAX_PAYLOAD = 128 << 20
BATCH_BYTES = 24 << 20
BATCH_RECORDS = 250_000

Record = tuple[bytes, bytes, bytes, bytes]
Batch = tuple[list[Record], ...]


class FastqArchiveError(ValueError):
    """Invalid FASTQ, mate pairing or archive."""


class Codec(Protocol):
    @property
    def magic(self) -> bytes: ...

    @property
    def block_bytes(self) -> int: ...

    def encode(self, batch: Batch) -> bytes: ...

    def decode(self, payload: bytes, mates: int) -> tuple[bytes, ...]: ...


class FastqReader(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


class FastqWriter(Protocol):
    def write(self, data: bytes, /) -> int: ...


@contextmanager
def open_fastq(path: Path) -> Iterator[FastqReader]:
    """Read plain FASTQ or gzip/BGZF, independent of the filename suffix."""
    with path.open("rb", buffering=1 << 20) as source:
        magic = source.read(2)
        source.seek(0)
        if magic == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=source) as uncompressed:
                yield uncompressed
        else:
            yield source


def line_content(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return line[:-2]
    return line.removesuffix(b"\n")


def read_records(source: FastqReader) -> Iterator[Record]:
    read = source.readline
    ordinal = 0
    while header := read(MAX_LINE + 1):
        ordinal += 1
        sequence, plus, quality = (
            read(MAX_LINE + 1),
            read(MAX_LINE + 1),
            read(MAX_LINE + 1),
        )
        if (
            not header.startswith(b"@")
            or not plus.startswith(b"+")
            or not quality
            or not sequence
            or max(len(header), len(sequence), len(plus), len(quality)) > MAX_LINE
            or len(line_content(sequence)) != len(line_content(quality))
        ):
            raise FastqArchiveError(f"Invalid four-line FASTQ at record {ordinal}")
        yield header, sequence, plus, quality


def _mate_id(header: bytes, mate: bytes) -> bytes | None:
    fields = header[1:].split(None, 1)
    if not fields:
        return None
    name = fields[0]
    if name.endswith((b"/1", b"/2")):
        if not name.endswith(b"/" + mate):
            return None
        name = name[:-2]
    if len(fields) == 2 and fields[1][:2] in (b"1:", b"2:"):
        if fields[1][:1] != mate:
            return None
    return name or None


def pair_matches(left: bytes, right: bytes) -> bool:
    name = _mate_id(left, b"1")
    return name is not None and name == _mate_id(right, b"2")


def batches(inputs: tuple[FastqReader, ...], block_bytes: int) -> Iterator[Batch]:
    first = read_records(inputs[0])
    second = read_records(inputs[1]) if len(inputs) == 2 else None
    batch: Batch = ([], []) if second is not None else ([],)
    size = 0
    groups = (
        zip_longest(first, second) if second is not None else ((r, None) for r in first)
    )
    for ordinal, (left, right) in enumerate(groups, 1):
        if left is None or (right is None and second is not None):
            raise FastqArchiveError(
                f"FASTQ mates have different record counts at pair {ordinal}"
            )
        if right is not None:
            if not pair_matches(left[0], right[0]):
                raise FastqArchiveError(f"FASTQ mates do not match at pair {ordinal}")
        pair_size = len(left[0]) + len(left[1]) + len(left[2]) + len(left[3])
        if right is not None:
            pair_size += len(right[0]) + len(right[1]) + len(right[2]) + len(right[3])
        if pair_size > BATCH_BYTES:
            raise FastqArchiveError(f"FASTQ record or pair {ordinal} exceeds 24 MiB")
        if size + pair_size > block_bytes or len(batch[0]) == BATCH_RECORDS:
            yield batch
            batch = ([], []) if second is not None else ([],)
            size = 0
        size += pair_size
        batch[0].append(left)
        if right is not None:
            batch[1].append(right)
    if batch[0]:
        yield batch


def write_archive(
    paths: tuple[Path, ...],
    destination: FastqWriter,
    codec: Codec,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Encode ordered read streams and finish with one SHA-256 per input."""
    with ExitStack() as stack:
        sources = tuple(stack.enter_context(open_fastq(path)) for path in paths)
        digests = [hashlib.sha256() for _ in sources]
        destination.write(codec.magic + bytes([len(sources)]))
        for batch in batches(sources, codec.block_bytes):
            if checkpoint:
                checkpoint()
            for digest, records in zip(digests, batch, strict=True):
                digest.update(b"".join(line for record in records for line in record))
            data = codec.encode(batch)
            if not 0 < len(data) <= MAX_PAYLOAD:
                raise FastqArchiveError("FASTQ archive block exceeds the format limit")
            destination.write(CHUNK.pack(len(data), zlib.crc32(data)))
            destination.write(data)
            # Do not keep the previous batch alive while the iterator fills the next.
            del batch
        destination.write(CHUNK.pack(0, 0))
        destination.write(b"".join(digest.digest() for digest in digests))


def read_exact(source: BinaryIO, size: int) -> bytes:
    data = source.read(size)
    if len(data) != size:
        raise FastqArchiveError("Truncated FASTQ archive")
    return data


def _payloads(
    source: BinaryIO, checkpoint: Callable[[], None] | None
) -> Iterator[bytes]:
    while True:
        if checkpoint:
            checkpoint()
        size, checksum = CHUNK.unpack(read_exact(source, CHUNK.size))
        if size == 0 and checksum == 0:
            return
        if not 0 < size <= MAX_PAYLOAD:
            raise FastqArchiveError("Invalid FASTQ archive block size")
        payload = read_exact(source, size)
        if zlib.crc32(payload) != checksum:
            raise FastqArchiveError("FASTQ archive block checksum mismatch")
        yield payload


def _decoded_blocks(
    payloads: Iterator[bytes], codec: Codec, mates: int
) -> Iterator[tuple[bytes, ...]]:
    first = next(payloads, None)
    if first is None:
        return
    with ContextThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(codec.decode, first, mates)
        for payload in payloads:
            decoded = pending.result()
            if any(not content.endswith(b"\n") for content in decoded):
                raise FastqArchiveError(
                    "Missing FASTQ line ending before the last block"
                )
            pending = pool.submit(codec.decode, payload, mates)
            yield decoded
        yield pending.result()


def restore_archive(
    source: BinaryIO,
    outputs: tuple[FastqWriter, ...],
    codec: Codec,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Decode to staging streams; the caller must only publish on success."""
    if read_exact(source, len(codec.magic)) != codec.magic:
        raise FastqArchiveError("Not a supported FASTQ archive version")
    mates = read_exact(source, 1)[0]
    if mates not in (1, 2) or mates != len(outputs):
        raise FastqArchiveError(
            "FASTQ archive and requested outputs have different mate counts"
        )
    digests = [hashlib.sha256() for _ in outputs]
    for decoded in _decoded_blocks(_payloads(source, checkpoint), codec, mates):
        for destination, digest, content in zip(outputs, digests, decoded, strict=True):
            digest.update(content)
            destination.write(content)
    if read_exact(source, 32 * mates) != b"".join(
        digest.digest() for digest in digests
    ):
        raise FastqArchiveError("Restored FASTQ SHA-256 checksum mismatch")
    if source.read(1):
        raise FastqArchiveError("Unexpected data after the FASTQ archive footer")
