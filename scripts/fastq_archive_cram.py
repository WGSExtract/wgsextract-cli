"""Benchmark candidate: CRAM 3.1 and a sparse, ordered literal repair stream."""

from __future__ import annotations

import io
import os
import re
import struct
import subprocess
import threading
import zlib
from contextlib import ExitStack, suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from wgsextract_cli.core.execution_context import context_thread
from wgsextract_cli.core.fastq_archive import (
    BATCH_BYTES,
    BATCH_RECORDS,
    Batch,
    FastqArchiveError,
    Record,
    read_exact,
    read_records,
)
from wgsextract_cli.core.utils import popen

_U32 = struct.Struct("<I")
_CRAM31 = b"CRAM\x03\x01"
_BLOCK_BYTES = 48 << 20
_NAME = rb"([A-Za-z0-9:_.-]{1,254})"
_PLAIN = re.compile(rb"@" + _NAME + rb"\n")
_CASAVA = re.compile(rb"@" + _NAME + rb" ([12]):N:0:([ACGTN]+\+[ACGTN]+)\n")
_BASES = re.compile(rb"[ACGTN]+\n")
_QUALITY = re.compile(rb"[!-~]+\n")


def _decoded_size(files: tuple[Path, ...]) -> int:
    total = 0
    for path in files:
        with suppress(FileNotFoundError):
            total += path.stat().st_size
    return total


def _watch_decoded_size(
    process: subprocess.Popen[bytes],
    files: tuple[Path, ...],
    done: threading.Event,
    oversized: threading.Event,
) -> None:
    while not done.wait(0.02):
        if _decoded_size(files) > _BLOCK_BYTES:
            oversized.set()
            with suppress(ProcessLookupError):
                process.kill()
            return


def _canonical_header(
    record: Record, mate: bytes, casava: bool
) -> tuple[bytes, bytes] | None:
    header, bases, plus, quals = record
    match = (_CASAVA if casava else _PLAIN).fullmatch(header)
    if (
        not match
        or (casava and match[2] != mate)
        or plus != b"+\n"
        or not _BASES.fullmatch(bases)
        or not _QUALITY.fullmatch(quals)
    ):
        return None
    return match[1], match[3] if casava else b""


def _repaired(content: bytes, repair: io.BytesIO, count: int) -> bytes:
    ordinary = iter(read_records(io.BytesIO(content)))
    output = io.BytesIO()
    last = 0
    for _ in range(count):
        ordinal, length = struct.unpack("<II", read_exact(repair, 8))
        if not last <= ordinal < BATCH_RECORDS or not 0 < length <= BATCH_BYTES:
            raise FastqArchiveError("Invalid FASTQ repair entry")
        for _ in range(ordinal - last):
            record = next(ordinary, None)
            if record is None:
                raise FastqArchiveError(
                    "FASTQ repair entry has no corresponding CRAM data"
                )
            output.writelines(record)
        output.write(read_exact(repair, length))
        last = ordinal + 1
        if output.tell() > _BLOCK_BYTES:
            raise FastqArchiveError("Repaired FASTQ exceeds the block limit")
    for record in ordinary:
        output.writelines(record)
    return output.getvalue()


class CramCodec:
    """Keep complete pairs in ordinal order; store unrepresentable pairs verbatim."""

    magic = b"WGSFQ\x01"
    block_bytes = _BLOCK_BYTES

    def __init__(self, samtools: list[str], scratch: Path) -> None:
        self.samtools = samtools
        self.scratch = scratch

    def _run(self, directory: Path, *args: str, files: tuple[Path, ...] = ()) -> bytes:
        # Reference resolution is local only, even if the caller exports REF_PATH.
        env = {
            **os.environ,
            "REF_PATH": ".",
            "REF_CACHE": "./%s",
        }
        with popen(
            [*self.samtools, *args],
            cwd=directory,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            if files:
                done, oversized = threading.Event(), threading.Event()
                watcher = context_thread(
                    target=_watch_decoded_size,
                    args=(process, files, done, oversized),
                    daemon=True,
                )
                watcher.start()
                try:
                    stdout, _ = process.communicate()
                finally:
                    done.set()
                    watcher.join()
                if oversized.is_set() or _decoded_size(files) > _BLOCK_BYTES:
                    raise FastqArchiveError(
                        "Decoded CRAM exceeds the FASTQ block limit"
                    )
            else:
                stdout, _ = process.communicate()
        if process.returncode:
            raise FastqArchiveError(
                f"samtools {args[0]} failed (exit {process.returncode})"
            )
        return stdout

    def encode(self, batch: Batch) -> bytes:
        with TemporaryDirectory(dir=self.scratch) as work:
            directory = Path(work)
            casava = len(batch) == 2 and any(
                _CASAVA.fullmatch(record[0]) for record in batch[0][:32]
            )
            cram, repairs = self._encode_chunk(directory, batch, casava)
            return struct.pack("<BI", casava, len(cram)) + cram + repairs

    def _encode_chunk(
        self, directory: Path, batch: Batch, casava: bool
    ) -> tuple[bytes, bytes]:
        files = tuple(f"r{i}.fq" for i in range(1, len(batch) + 1))
        repairs = [io.BytesIO() for _ in batch]
        count = 0
        previous = None
        with ExitStack() as stack:
            streams = [
                stack.enter_context((directory / name).open("wb")) for name in files
            ]
            for ordinal, records in enumerate(zip(*batch, strict=True)):
                header = _canonical_header(records[0], b"1", casava)
                if (
                    header
                    and header[0] != previous
                    and (
                        len(batch) == 1
                        or header == _canonical_header(records[1], b"2", casava)
                    )
                ):
                    for stream, record in zip(streams, records, strict=True):
                        stream.writelines(record)
                    previous = header[0]
                else:
                    for repair, record in zip(repairs, records, strict=True):
                        data = b"".join(record)
                        repair.write(struct.pack("<II", ordinal, len(data)) + data)
                    count += 1
        cram = b""
        if count < len(batch[0]):
            inputs = (
                ["-0", files[0]]
                if len(files) == 1
                else ["-1", files[0], "-2", files[1]]
            )
            cram = self._run(
                directory,
                "import",
                "--no-PG",
                *(["-i"] if casava else []),
                *inputs,
                "-O",
                "CRAM,version=3.1,no_ref=1",
            )
        side_stream = (
            zlib.compress(
                _U32.pack(count) + b"".join(repair.getvalue() for repair in repairs),
                level=3,
            )
            if count
            else b""
        )
        return cram, side_stream

    def _decode_cram(
        self, payload: bytes, mates: int, casava: int
    ) -> tuple[bytes, ...]:
        if not payload.startswith(_CRAM31):
            raise FastqArchiveError("Expected an unaligned CRAM 3.1 payload")
        with TemporaryDirectory(dir=self.scratch) as work:
            directory = Path(work)
            (directory / "reads.cram").write_bytes(payload)
            header = self._run(directory, "view", "-H", "reads.cram")
            if any(line.startswith(b"@SQ") for line in header.split(b"\n")):
                raise FastqArchiveError(
                    "A FASTQ archive cannot contain CRAM references"
                )
            index = ["-i", "--index-format", "i*i*"] if casava else []
            files = tuple(directory / f"r{i}.fq" for i in range(1, mates + 1))
            parts = ["-0", "r1.fq"]
            if mates == 2:
                parts = [
                    "-1",
                    "r1.fq",
                    "-2",
                    "r2.fq",
                    "-0",
                    os.devnull,
                    "-s",
                    os.devnull,
                ]
            self._run(
                directory,
                "fastq",
                "-n",
                *index,
                *parts,
                "reads.cram",
                files=files,
            )
            return tuple(path.read_bytes() for path in files)

    def decode(self, payload: bytes, mates: int) -> tuple[bytes, ...]:
        source = io.BytesIO(payload)
        casava, length = struct.unpack("<BI", read_exact(source, 5))
        if casava not in (0, 1) or (casava and mates == 1) or length > len(payload) - 5:
            raise FastqArchiveError("Invalid FASTQ CRAM payload header")
        decoded = (b"",) * mates
        if length:
            decoded = self._decode_cram(read_exact(source, length), mates, casava)
        if side_stream := source.read():
            inflater = zlib.decompressobj()
            limit = _BLOCK_BYTES + 8 * BATCH_RECORDS * mates + 4
            unfolded = inflater.decompress(side_stream, limit + 1)
            if len(unfolded) > limit or not inflater.eof or inflater.unused_data:
                raise FastqArchiveError("Invalid FASTQ repair stream")
            repair = io.BytesIO(unfolded)
            count = _U32.unpack(read_exact(repair, 4))[0]
            if not 0 < count <= BATCH_RECORDS:
                raise FastqArchiveError("Invalid FASTQ repair count")
            decoded = tuple(_repaired(data, repair, count) for data in decoded)
            if repair.read(1):
                raise FastqArchiveError("Unexpected bytes after FASTQ repair entries")
        if not 0 < sum(map(len, decoded)) <= _BLOCK_BYTES:
            raise FastqArchiveError("Invalid decoded FASTQ block size")
        return decoded
