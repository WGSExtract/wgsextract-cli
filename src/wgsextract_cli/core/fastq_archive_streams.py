"""Lossless FASTQ archive codec: four independently compressed columns per mate."""

from __future__ import annotations

import io
import struct
import sys
from functools import lru_cache
from importlib import import_module
from itertools import chain

import numpy as np

from wgsextract_cli.core.fastq_archive import (
    BATCH_BYTES,
    BATCH_RECORDS,
    MAX_LINE,
    MAX_PAYLOAD,
    Batch,
    FastqArchiveError,
    pair_matches,
    read_exact,
)
from wgsextract_cli.core.utils import MissingDependencyError

_FIELD = struct.Struct("<II")
_PACK = struct.Struct("<BI")
_ALPHABETS = (b"", b"ACGT", b"", b"F:,#")


@lru_cache
def _decode_table(alphabet: bytes) -> np.ndarray:
    return np.frombuffer(
        bytes(
            alphabet[(code >> shift) & 3]
            for code in range(256)
            for shift in (0, 2, 4, 6)
        ),
        dtype="<u4",
    )


def _pack(data: bytes, alphabet: bytes) -> bytes:
    if not alphabet:
        return data
    table = bytes(253 if c not in alphabet else alphabet.index(c) for c in range(256))
    translated = data.translate(table)
    escape_count = translated.count(b"\xfd")
    if not data or escape_count > len(data) // 16:
        return _PACK.pack(0, len(data)) + data
    codes = np.frombuffer(translated.ljust((len(data) + 3) & ~3, b"\0"), dtype=np.uint8)
    positions = np.flatnonzero(codes == 253)
    columns = (codes & 3).reshape((-1, 4))
    packed = (
        columns[:, 0]
        | (columns[:, 1] << 2)
        | (columns[:, 2] << 4)
        | (columns[:, 3] << 6)
    )
    delta = np.diff(positions, prepend=0).astype("<u4")
    escaped = np.frombuffer(data, dtype=np.uint8)[positions]
    return b"".join(
        (_PACK.pack(1, len(data)), packed.tobytes(), delta.tobytes(), escaped.tobytes())
    )


def _unpack(data: bytes, alphabet: bytes) -> bytes:
    if not alphabet:
        return data
    if len(data) < _PACK.size:
        raise FastqArchiveError("Missing FASTQ packed stream header")
    mode, size = _PACK.unpack_from(data)
    if size > BATCH_BYTES:
        raise FastqArchiveError("Invalid unpacked FASTQ stream length")
    data = data[_PACK.size :]
    if mode == 0 and len(data) == size:
        return data
    packed_size = (size + 3) // 4
    remainder = len(data) - packed_size
    if mode != 1 or remainder < 0 or remainder % 5:
        raise FastqArchiveError("Invalid FASTQ packed stream")
    size_escapes = remainder // 5
    encoded = np.frombuffer(data, dtype=np.uint8, count=packed_size)
    restored = _decode_table(alphabet)[encoded].view(np.uint8)[:size]
    if size_escapes:
        deltas = np.frombuffer(data, "<u4", count=size_escapes, offset=packed_size)
        positions = np.cumsum(deltas, dtype=np.uint64)
        if positions[-1] >= size:
            raise FastqArchiveError("Invalid FASTQ packed stream escape position")
        restored[positions] = np.frombuffer(
            data, dtype=np.uint8, offset=len(data) - size_escapes
        )
    return bytes(restored.tobytes())


class StreamCodec:
    magic = b"WGSFS\x01"
    block_bytes = BATCH_BYTES

    def __init__(self) -> None:
        try:
            if sys.version_info >= (3, 14):
                self.zstd = import_module("compression.zstd")
            else:
                self.zstd = import_module("backports.zstd")
        except ImportError as error:
            raise MissingDependencyError(
                "The Zstandard Python module is required for FASTQ archives. "
                "Install the project's Python dependencies before using this command."
            ) from error

    def encode(self, batch: Batch) -> bytes:
        output = io.BytesIO()
        for mate in batch:
            for lines, alphabet, level in zip(
                zip(*mate, strict=True),
                _ALPHABETS,
                (3, 1, 1, 3),
                strict=True,
            ):
                data = _pack(b"".join(lines), alphabet)
                encoded = self.zstd.compress(data, level=level)
                output.write(_FIELD.pack(len(data), len(encoded)))
                output.write(encoded)
        return output.getvalue()

    def decode(self, payload: bytes, mates: int) -> tuple[bytes, ...]:
        source = io.BytesIO(payload)
        result: list[bytes] = []
        decoded_bytes = 0
        first_headers: list[bytes] | None = None
        for _ in range(mates):
            columns: list[list[bytes]] = []
            final_lf = False
            for i, alphabet in enumerate(_ALPHABETS):
                raw, encoded = _FIELD.unpack(read_exact(source, _FIELD.size))
                if raw > BATCH_BYTES + _PACK.size or encoded > MAX_PAYLOAD:
                    raise FastqArchiveError("Invalid FASTQ stream length")
                decoder = self.zstd.ZstdDecompressor(
                    options={self.zstd.DecompressionParameter.window_log_max: 26},
                )
                try:
                    data = decoder.decompress(
                        read_exact(source, encoded), max_length=raw + 1
                    )
                except self.zstd.ZstdError as error:
                    raise FastqArchiveError("Invalid Zstandard FASTQ stream") from error
                if len(data) != raw or not decoder.eof or decoder.unused_data:
                    raise FastqArchiveError("Unexpected decoded FASTQ stream length")
                data = _unpack(data, alphabet)
                decoded_bytes += len(data)
                if decoded_bytes > BATCH_BYTES:
                    raise FastqArchiveError("Decoded FASTQ exceeds the block limit")
                final_lf = data.endswith(b"\n")
                if i != 3 and not final_lf:
                    raise FastqArchiveError("Incomplete FASTQ stream")
                count = data.count(b"\n") + (not final_lf)
                if not 0 < count <= BATCH_RECORDS:
                    raise FastqArchiveError("Invalid FASTQ block record count")
                lines = data.split(b"\n")
                if final_lf:
                    lines.pop()
                columns.append(lines)
            if len({len(column) for column in columns}) != 1:
                raise FastqArchiveError("FASTQ stream record counts differ")
            _validate_columns(columns, final_lf)
            headers = columns[0]
            if mates == 2:
                if first_headers is None:
                    first_headers = headers
                elif len(first_headers) != len(headers) or not all(
                    pair_matches(left, right)
                    for left, right in zip(first_headers, headers, strict=True)
                ):
                    raise FastqArchiveError("Decoded FASTQ mates do not match")
            rebuilt = b"\n".join(chain.from_iterable(zip(*columns, strict=True)))
            result.append(rebuilt + (b"\n" if final_lf else b""))
        if source.read(1):
            raise FastqArchiveError("Unexpected bytes after FASTQ streams")
        return tuple(result)


def _validate_columns(columns: list[list[bytes]], final_lf: bool) -> None:
    headers, bases, plus, quals = columns
    for i, (name, sequence, separator, quality) in enumerate(
        zip(headers, bases, plus, quals, strict=True), 1
    ):
        quality_has_lf = final_lf or i != len(quals)
        if (
            not name.startswith(b"@")
            or not separator.startswith(b"+")
            or (not quality and not quality_has_lf)
            or max(len(name), len(sequence), len(separator)) + 1 > MAX_LINE
            or len(quality) + quality_has_lf > MAX_LINE
            or len(sequence) - sequence.endswith(b"\r")
            != len(quality) - (quality_has_lf and quality.endswith(b"\r"))
        ):
            raise FastqArchiveError("Invalid decoded four-line FASTQ")
