"""Append coordinate-sorted BAM or CRAM pieces into one file without
re-encoding them, as ``samtools cat`` does, but one piece at a time so that
each piece can be deleted once it is appended.

Each piece is a complete file whose header is the same as the first piece's.
The first piece's file definition and header are kept, then each piece's
records follow; ``finish`` adds the end-of-file marker. The caller makes the
pieces in coordinate order, so the result is sorted, and indexes it.
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import BinaryIO

from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.utils import WGSExtractError

# The empty BGZF block that ends every BAM (SAM spec 4.1.2).
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
# The empty container that ends every CRAM 3.x file (CRAM spec 9).
CRAM3_EOF = bytes.fromhex(
    "0f000000ffffffff0fe0454f4600000000010005bdd94f0001000606010001000100ee63014b"
)
_CRAM_FILE_DEFINITION = 26
_COPY_BUFFER = 8 * 1024 * 1024


class SpliceError(WGSExtractError):
    """A piece can't be appended: not a complete file of the same layout."""


def _is_cram(path: PathInput) -> bool:
    return os.fspath(path).lower().endswith(".cram")


def _itf8(data: bytes, offset: int) -> tuple[int, int]:
    """The CRAM ITF8 integer at ``offset`` and the offset after it."""
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    if first < 0xC0:
        return (first & 0x3F) << 8 | data[offset + 1], offset + 2
    if first < 0xE0:
        return (first & 0x1F) << 16 | int.from_bytes(
            data[offset + 1 : offset + 3]
        ), offset + 3
    if first < 0xF0:
        return (first & 0x0F) << 24 | int.from_bytes(
            data[offset + 1 : offset + 4]
        ), offset + 4
    value = int.from_bytes(data[offset + 1 : offset + 4]) << 4 | data[offset + 4] & 0x0F
    return (first & 0x0F) << 28 | value, offset + 5


def _skip_ltf8(data: bytes, offset: int) -> int:
    """The offset after the CRAM LTF8 integer at ``offset``: its first
    byte's leading one bits count the bytes that follow."""
    extra = 0
    while extra < 8 and data[offset] & (0x80 >> extra):
        extra += 1
    return offset + 1 + extra


def _cram_layout(stream: BinaryIO, path: PathInput) -> tuple[bytes, int]:
    """The CRAM's header container and the offset where its records start."""
    definition = stream.read(_CRAM_FILE_DEFINITION)
    if len(definition) < _CRAM_FILE_DEFINITION or definition[:4] != b"CRAM":
        raise SpliceError(f"{path} is not a CRAM file.")
    if definition[4] != 3:
        raise SpliceError(f"{path} is CRAM {definition[4]}.{definition[5]}, not 3.x.")
    # The container header: int32 length; ITF8 reference, start, span and
    # records; LTF8 record counter and bases; ITF8 blocks and landmarks; CRC32.
    head = stream.read(4096)
    try:
        (length,) = struct.unpack_from("<i", head)
        offset = 4
        for _ in range(4):
            offset = _itf8(head, offset)[1]
        offset = _skip_ltf8(head, _skip_ltf8(head, offset))
        offset = _itf8(head, offset)[1]
        landmarks, offset = _itf8(head, offset)
        for _ in range(landmarks):
            offset = _itf8(head, offset)[1]
    except (IndexError, struct.error):
        raise SpliceError(f"{path} ends inside its header container.") from None
    container = offset + 4 + length
    stream.seek(_CRAM_FILE_DEFINITION)
    return stream.read(container), _CRAM_FILE_DEFINITION + container


def _bam_header_length(data: bytes) -> int | None:
    """The length of the BAM header at the start of ``data``, decompressed,
    or None if ``data`` ends before it does."""
    if len(data) < 8:
        return None
    (text,) = struct.unpack_from("<i", data, 4)
    offset = 8 + text
    if len(data) < offset + 4:
        return None
    (references,) = struct.unpack_from("<i", data, offset)
    offset += 4
    for _ in range(references):
        if len(data) < offset + 4:
            return None
        (name,) = struct.unpack_from("<i", data, offset)
        offset += 4 + name + 4
    return offset if len(data) >= offset else None


def _bam_layout(stream: BinaryIO, path: PathInput) -> tuple[bytes, int]:
    """The BAM's decompressed header and the offset of the BGZF block where
    its records start. htslib flushes the header into blocks of its own."""
    header = b""
    offset = 0
    while True:
        fixed = stream.read(12)
        if len(fixed) < 12 or fixed[:4] != b"\x1f\x8b\x08\x04":
            raise SpliceError(f"{path} is not a BGZF-compressed BAM file.")
        (extra_length,) = struct.unpack_from("<H", fixed, 10)
        extra = stream.read(extra_length)
        size = None
        position = 0
        while position + 4 <= len(extra):
            tag, length = (
                extra[position : position + 2],
                struct.unpack_from("<H", extra, position + 2)[0],
            )
            if tag == b"BC" and length == 2:
                size = struct.unpack_from("<H", extra, position + 4)[0] + 1
            position += 4 + length
        if size is None:
            raise SpliceError(f"{path} has a gzip block without a BGZF size.")
        body = stream.read(size - 12 - extra_length)
        header += zlib.decompress(body[:-8], wbits=-15)
        offset += size
        if header[:4] != b"BAM\x01"[: len(header)]:
            raise SpliceError(f"{path} is not a BAM file.")
        length = _bam_header_length(header)
        if length is not None:
            if length != len(header):
                raise SpliceError(
                    f"{path}'s header shares a compressed block with its records."
                )
            return header, offset


def _layout(path: PathInput) -> tuple[bytes, int, int]:
    """A piece's header, and the byte range of its records (without the
    end-of-file marker, which it must have: a complete file)."""
    size = Path(path).stat().st_size
    eof = CRAM3_EOF if _is_cram(path) else BGZF_EOF
    with open(path, "rb") as handle:
        header, start = (_cram_layout if _is_cram(path) else _bam_layout)(handle, path)
        handle.seek(max(0, size - len(eof)))
        if size - len(eof) < start or handle.read() != eof:
            raise SpliceError(f"{path} is truncated: it has no end-of-file marker.")
    return header, start, size - len(eof)


def _copy(source: PathInput, output: BinaryIO, start: int, end: int) -> None:
    with open(source, "rb") as handle:
        handle.seek(start)
        remaining = end - start
        while remaining:
            block = handle.read(min(_COPY_BUFFER, remaining))
            if not block:
                raise SpliceError(f"{source} shrank while it was being appended.")
            output.write(block)
            remaining -= len(block)


def append(target: PathInput, piece: PathInput, size: int) -> int:
    """Append ``piece``'s records to ``target``, first cutting ``target``
    back to ``size`` bytes (what earlier appends committed; 0 starts it with
    ``piece``'s header). Returns ``target``'s new committed size, after
    syncing it to disk."""
    header, start, end = _layout(piece)
    target_path = Path(target)
    if size:
        with open(target_path, "rb") as handle:
            existing = (_cram_layout if _is_cram(target) else _bam_layout)(
                handle, target
            )[0]
        if existing != header:
            raise SpliceError(
                f"{piece}'s header differs from the header of {target_path.name}."
            )
    with open(target_path, "r+b" if size else "wb") as output:
        output.truncate(size)
        output.seek(size)
        _copy(piece, output, 0 if not size else start, end)
        output.flush()
        os.fsync(output.fileno())
        return output.tell()


def finish(target: PathInput, size: int) -> None:
    """End ``target``, cut back to ``size`` committed bytes, with its
    end-of-file marker."""
    with open(target, "r+b") as output:
        output.truncate(size)
        output.seek(size)
        output.write(CRAM3_EOF if _is_cram(target) else BGZF_EOF)
        output.flush()
        os.fsync(output.fileno())
