"""Benchmark two byte-exact FASTQ archive prototypes against bgzip -6."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import resource
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory

from fastq_archive_cram import CramCodec

from wgsextract_cli.core.fastq_archive import (
    Codec,
    line_content,
    open_fastq,
    read_records,
    restore_archive,
    write_archive,
)
from wgsextract_cli.core.fastq_archive_streams import StreamCodec

_METHODS = ("bgzip", "cram31-repair", "stream-zstd")
EDGE_MATES = (
    b"@standard 1:N:0:AC+TG\nACNN\n+\nII##\n"
    b"@standard 1:N:0:AC+TG\nACGT\n+\nIIII\n"
    b"@dual 1:N:0:AC+TG\nACG\n+\nIII\n"
    b"@dup/1 other \ttext\r\nacR.Y\r\n+some text\r\n~@%#+\r\n"
    b"@dup/1\nG\n+\n!",
    b"@standard 2:N:0:AC+TG\nTNAC\n+\n#III\n"
    b"@standard 2:N:0:AC+TG\nTGCA\n+\nIIII\n"
    b"@dual 2:N:0:CA+GT\nTGC\n+\n#II\n"
    b"@dup/2 2:N:0:TAG-TTT\r\nNry\r\n+odd\r\nIII\r\n"
    b"@dup/2\r\nAC\n+\r\n!#",
)


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"Cannot benchmark without {name} on PATH")
    return str(Path(found).resolve())


def _codec(method: str, directory: Path) -> Codec:
    if method == "cram31-repair":
        return CramCodec([_tool("samtools")], directory)
    return StreamCodec()


def _worker(method: str, action: str, work: Path) -> None:
    source = (work / "r1.fq", work / "r2.fq")
    output = (work / "restore1.fq", work / "restore2.fq")
    archive = work / "reads.wgfq"
    started = time.perf_counter()
    if method == "bgzip":
        executable = _tool("bgzip")
        for i, (original, restored) in enumerate(zip(source, output, strict=True)):
            compressed = work / f"mate{i}.bgz"
            command = (
                [executable, "-l", "6", "-c", str(original)]
                if action == "encode"
                else [executable, "-dc", str(compressed)]
            )
            with (compressed if action == "encode" else restored).open("wb") as stream:
                subprocess.run(command, stdout=stream, check=True)
    elif action == "encode":
        with archive.open("wb") as stream:
            write_archive(source, stream, _codec(method, work))
    else:
        with archive.open("rb") as stream, ExitStack() as stack:
            destinations = tuple(
                stack.enter_context(path.open("wb")) for path in output
            )
            restore_archive(stream, destinations, _codec(method, work))
    elapsed = time.perf_counter() - started
    rss = max(
        resource.getrusage(kind).ru_maxrss
        for kind in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)
    )
    print(json.dumps([elapsed, rss / (1 << (20 if sys.platform == "darwin" else 10))]))


def _measure(method: str, action: str, work: Path) -> tuple[float, float]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        method,
        action,
        str(work),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{method} {action} failed:\n{result.stderr}")
    seconds, rss = json.loads(result.stdout)
    return float(seconds), float(rss)


def _source_stats(
    source: Path, output: Path, records: int | None
) -> tuple[int, int, bytes]:
    bases = size = count = 0
    digest = hashlib.sha256()
    with open_fastq(source) as stream, output.open("wb") as raw:
        for record in islice(read_records(stream), records):
            data = b"".join(record)
            raw.write(data)
            digest.update(data)
            size += len(data)
            bases += len(line_content(record[1]))
            count += 1
    if (records is not None and count != records) or bases == 0:
        raise ValueError(f"Input {source.name} has too few FASTQ records or no bases")
    return size, bases, digest.digest()


def _sha(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.digest()


def _run(
    method: str, work: Path, size: int, bases: int, expected: list[bytes], baseline: int
) -> list[object]:
    encode, encode_rss = _measure(method, "encode", work)
    decode, decode_rss = _measure(method, "decode", work)
    archives = list(work.glob("*.bgz")) if method == "bgzip" else [work / "reads.wgfq"]
    archive_bytes = sum(path.stat().st_size for path in archives)
    restores = [work / "restore1.fq", work / "restore2.fq"]
    exact = [_sha(path) for path in restores] == expected
    for path in (*archives, *restores):
        path.unlink()
    return [
        method,
        archive_bytes,
        8 * archive_bytes / bases,
        (baseline or archive_bytes) / archive_bytes,
        size / encode / 1e6,
        size / decode / 1e6,
        encode_rss,
        decode_rss,
        exact,
    ]


def main() -> int:
    if sys.argv[1:2] == ["--worker"]:
        _, method, action, directory = sys.argv[1:]
        _worker(method, action, Path(directory))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("r1", type=Path, nargs="?")
    parser.add_argument("r2", type=Path, nargs="?")
    parser.add_argument(
        "--records", type=int, help="Use only this many records per mate"
    )
    parser.add_argument(
        "--edge", action="store_true", help="Run the synthetic edge fixture instead"
    )
    parser.add_argument(
        "--method",
        choices=_METHODS,
        action="append",
        help="Benchmark only the selected method(s)",
    )
    parser.add_argument(
        "--bgzip-bytes",
        type=int,
        help="Previously measured bgzip size for the identical input",
    )
    parser.add_argument(
        "--csv", type=Path, required=True, help="Write bench results to a new CSV"
    )
    args = parser.parse_args()
    if not args.edge and (args.r1 is None or args.r2 is None):
        parser.error("Pass both FASTQ mates, or use --edge")
    if args.records is not None and args.records < 1:
        parser.error("--records must be positive")
    methods = args.method or _METHODS
    if "bgzip" not in methods and not (args.bgzip_bytes and args.bgzip_bytes > 0):
        parser.error("Provide --bgzip-bytes when skipping the bgzip run")
    parent = args.csv.resolve().parent
    if shutil.disk_usage(parent).free < 30 << 30:
        parser.error("Benchmark requires at least 30 GiB of available disk space")
    with TemporaryDirectory(prefix="fastq-bench-", dir=parent) as temp:
        work = Path(temp)
        if args.edge:
            stats = []
            for i, data in enumerate(EDGE_MATES, 1):
                (work / f"r{i}.fq").write_bytes(data)
                stats.append(
                    (
                        len(data),
                        sum(
                            len(line_content(line + b"\n"))
                            for line in data.split(b"\n")[1::4]
                        ),
                        hashlib.sha256(data).digest(),
                    )
                )
        else:
            stats = [
                _source_stats(source, work / f"r{i}.fq", args.records)
                for i, source in enumerate((args.r1, args.r2), 1)
            ]
        size, bases, expected = zip(*stats, strict=True)
        ok = True
        with args.csv.open("x", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                [
                    "method",
                    "bytes",
                    "bits_per_base",
                    "bgzip_over_archive",
                    "encode_mb_s",
                    "decode_mb_s",
                    "encode_rss_mib",
                    "decode_rss_mib",
                    "exact_sha256",
                ]
            )
            baseline = args.bgzip_bytes or 0
            for method in sorted(methods, key=_METHODS.index):
                row = _run(
                    method, work, sum(size), sum(bases), list(expected), baseline
                )
                if method == "bgzip":
                    baseline = int(str(row[1]))
                ok &= bool(row[-1])
                writer.writerow(row)
                csv_file.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
