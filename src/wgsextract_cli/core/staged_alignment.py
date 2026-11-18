"""Compressed, resumable BWA/duplicate-marking phases sized independently."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from wgsextract_cli.core.alignment.profiles import (
    OUTPUT_PROFILES,
    output_profile_options,
)
from wgsextract_cli.core.alignment.samtools_commands import indexed_output, parse_memory
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.execution_context import context_thread
from wgsextract_cli.core.results import current_result, fingerprint, warn
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import WGSExtractError, run_command, run_pipeline

GIB = 1024**3
_FAN_IN = 64


def safety_reserve(reads: int | None) -> int:
    return int(min(25 * GIB, max(64 * 1024**2, (reads or 680_000_000) * 64)) * 1.08)


def _sync_directory(path: Path) -> None:
    if sys.platform != "win32":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sync_file(path: Path) -> None:
    # Windows _commit requires a descriptor opened with write access.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _save(path: Path, value: Any) -> None:
    staged = path.with_suffix(".new")
    with staged.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    staged.replace(path)
    _sync_directory(path.parent)


def _identity(path: Path) -> dict[str, Any]:
    value = fingerprint(path)
    if value is None:
        raise WGSExtractError(f"Cannot fingerprint staged input {path}")
    return {"bytes": path.stat().st_size, "fingerprint": value}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _part_path(workspace: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise WGSExtractError("Invalid staged checkpoint path")
    path = workspace / relative
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise WGSExtractError("Staged checkpoint escapes its workspace")
    return path


def _remember_rg(line: bytes) -> bytes:
    if b"\tzI:" in line or b"\tPG:" in line:
        raise WGSExtractError("Staged BWA output has an unexpected zI/PG auxiliary tag")
    start = line.find(b"\tRG:")
    position = line.count(b"\t", 0, start) - 10 if start >= 0 else -1
    return line[:-1] + b"\tzI:i:" + str(position).encode() + b"\n"


def _restore_rg(line: bytes) -> bytes:
    marker = line.find(b"\tzI:i:")
    if marker < 0:
        raise WGSExtractError("Staged checkpoint is missing its RG-order marker")
    end = line.find(b"\t", marker + 1)
    end = len(line) - 1 if end < 0 else end
    position = int(line[marker + 6 : end])
    line = line[:marker] + line[end:]
    start = line.find(b"\tRG:")
    if position < 0:
        if start >= 0:
            raise WGSExtractError("Merge added an unexpected RG tag")
        return line
    if start < 0:
        raise WGSExtractError("Merge lost an RG tag")
    end = line.find(b"\t", start + 1)
    end = len(line) - 1 if end < 0 else end
    rg = line[start:end]
    remaining = line[:start] + line[end:]
    fields = remaining.split(b"\t", 11 + position)
    if len(fields) < 11 + position:
        raise WGSExtractError("Invalid staged RG-order marker")
    if len(fields) == 11 + position:
        return remaining[:-1] + rg + b"\n"
    return b"\t".join(fields[:-1]) + rg + b"\t" + fields[-1]


def workspace_path(out_path: str) -> Path:
    configured = configured_temp_directory()
    parent = Path(configured) if configured else Path(out_path).parent
    # The command's private run folder is removed on exit; checkpoints are siblings.
    if parent.name.startswith(".wgse_run_"):
        parent = parent.parent
    key = hashlib.sha256(str(Path(out_path).resolve()).encode()).hexdigest()[:16]
    return parent / f".{Path(out_path).name}.staged-{key}"


def _contract(
    commands: Sequence[Sequence[object]], memory: str, chunks: int
) -> dict[str, Any]:
    normalized = []
    for command in commands:
        args: list[Any] = []
        for index, argument in enumerate(command):
            value = str(argument)
            if (
                index
                and str(command[index - 1]) == "-T"
                and "collate" in map(str, command)
            ):
                args.append("<collation-scratch>")
                continue
            path = Path(shutil.which(value) or value) if index == 0 else Path(value)
            # Recreated temporary FASTQs have new paths but the same content.
            args.append(_identity(path) if path.is_file() else value)
        normalized.append(args)
    return {"commands": normalized, "memory": memory, "chunk_records": chunks}


@contextmanager
def _disk_guard(paths: Sequence[Path], reads: int | None) -> Iterator[threading.Event]:
    limit = safety_reserve(reads)
    cancelled = threading.Event()
    finished = threading.Event()
    minimum = [min(shutil.disk_usage(path).free for path in paths)]
    if minimum[0] < limit:
        raise WGSExtractError(
            "Not enough free disk for staged alignment's safety reserve"
        )

    def watch() -> None:
        while not finished.wait(1):
            try:
                free = min(shutil.disk_usage(path).free for path in paths)
                minimum[0] = min(minimum[0], free)
                if free < limit:
                    cancelled.set()
                    return
            except OSError:
                cancelled.set()
                return

    thread = context_thread(target=watch, daemon=True)
    thread.start()
    try:
        yield cancelled
    except WGSExtractError as error:
        if cancelled.is_set():
            raise WGSExtractError(
                "Staged alignment stopped to preserve free disk"
            ) from error
        raise
    finally:
        finished.set()
        thread.join()
        current_result().add_metrics(staged_alignment_minimum_free_bytes=minimum[0])


def run_staged_alignment(
    commands: Sequence[Sequence[object]],
    out_path: str,
    staged_output: str,
    *,
    memory: str,
    fmt: str,
    ref: str,
    threads: str = "1",
    compression_threads: str | None = None,
    retire_fastqs: Sequence[str] = (),
    read_length: float | None = None,
    expected_reads: int | None = None,
    chunk_records: int | None = None,
    output_profile: str = "default",
) -> Path:
    """Keep validated stages on failure; caller removes work only after output checks."""
    workspace = workspace_path(out_path)
    if workspace.is_symlink():
        raise WGSExtractError("Staged workspace must not be a symbolic link")
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    if sys.platform != "win32":
        info = workspace.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise WGSExtractError(
                "Staged workspace must be owned by this user with mode 0700"
            )
    amount, unit = parse_memory(memory)
    size = int(amount * (GIB if unit == "G" else 1024**2))
    chunks = chunk_records or max(
        1,
        int(
            2_000_000
            * min(size, 2 * GIB)
            / (2 * GIB)
            * min(1, 150 / (read_length or 150))
        ),
    )
    contract = _contract(commands, memory, chunks)
    contract["threads"] = threads
    compression_threads = compression_threads or threads
    contract["compression_threads"] = compression_threads
    contract["indexes"] = [
        _identity(Path(ref + suffix))
        for suffix in (".bwt", ".sa", ".pac", ".alt")
        if Path(ref + suffix).is_file()
    ]
    state = workspace / "alignment.json"
    raw = workspace / "unsorted.bam"
    manifest_path = workspace / "parts.json"
    samtools = get_tool_path("samtools")
    samblaster = get_tool_path("samblaster")
    if not samtools or not samblaster:
        raise WGSExtractError(
            "Staged alignment requires installed samtools and samblaster"
        )
    worker = [
        sys.executable,
        "-m",
        "wgsextract_cli.core.staged_alignment",
        "--threads",
        threads,
        "--compression-threads",
        compression_threads,
    ]
    try:
        with _disk_guard(
            [workspace.parent, Path(out_path).parent], expected_reads
        ) as cancel:
            previous = json.loads(state.read_text()) if state.exists() else None
            if previous and previous["contract"] != contract:
                raise WGSExtractError(
                    f"Staged checkpoint inputs/settings changed; remove {workspace} to discard it"
                )
            manifest = (
                json.loads(manifest_path.read_text())
                if manifest_path.exists()
                else None
            )
            complete = bool(manifest and manifest.get("complete"))
            if complete and previous is None:
                raise WGSExtractError("Staged checkpoint has no alignment contract")
            if not complete:
                if previous:
                    if _identity(raw) != previous["output"]:
                        raise WGSExtractError("Staged alignment checkpoint changed")
                else:
                    partial = workspace / "unsorted.partial.bam"
                    partial.unlink(missing_ok=True)
                    run_pipeline(
                        [
                            *commands,
                            [
                                samtools,
                                "view",
                                "-@",
                                compression_threads,
                                "-b",
                                "--no-PG",
                                "-o",
                                partial,
                                "-",
                            ],
                        ],
                        description="Staged Metal alignment",
                        cancel_event=cancel,
                        stderr_dir=workspace,
                        heartbeat=True,
                        expected_reads=expected_reads,
                    )
                    run_command([samtools, "quickcheck", "-v", partial])
                    _sync_file(partial)
                    partial.replace(raw)
                    _save(state, {"contract": contract, "output": _identity(raw)})
            for path in retire_fastqs:
                Path(path).unlink(missing_ok=True)
            if not complete:
                run_pipeline(
                    [
                        [samtools, "view", "-h", "--no-PG", raw],
                        [samblaster],
                        [
                            *worker,
                            "mark",
                            "--workspace",
                            workspace,
                            "--samtools",
                            samtools,
                            "--memory",
                            memory,
                            "--chunk-records",
                            chunks,
                        ],
                    ],
                    description="Staged duplicate marking/sorting",
                    cancel_event=cancel,
                    stderr_dir=workspace,
                    heartbeat=True,
                )
                manifest = json.loads(manifest_path.read_text())
                manifest["complete"] = True
                _save(manifest_path, manifest)
                raw.unlink()
            assert manifest is not None
            if complete:
                for part in manifest["parts"]:
                    if _digest(_part_path(workspace, part["name"])) != part["sha256"]:
                        raise WGSExtractError("Staged sorted checkpoint changed")
            run_pipeline(
                [
                    [
                        *worker,
                        "merge",
                        "--workspace",
                        workspace,
                        "--samtools",
                        samtools,
                        "--output",
                        staged_output,
                        "--format",
                        fmt,
                        "--reference",
                        ref,
                        "--output-profile",
                        output_profile,
                    ]
                ],
                description="Staged merge",
                cancel_event=cancel,
                stderr_dir=workspace,
                heartbeat=True,
            )
            current_result().add_metrics(
                alignment_mode="staged_metal",
                staged_alignment_checkpoint="compressed_bam",
                staged_alignment_chunk_records=chunks,
                staged_alignment_compression_level=3,
            )
        return workspace
    except BaseException:
        warn(
            "staged_alignment_retained",
            f"Staged checkpoints retained in {workspace}; rerun unchanged inputs to resume.",
            workspace=str(workspace),
        )
        raise


def _mark(options: argparse.Namespace) -> None:
    workspace: Path = options.workspace
    parts_dir = workspace / "parts"
    if parts_dir.is_symlink():
        raise WGSExtractError("Staged parts folder must not be a symbolic link")
    parts_dir.mkdir(exist_ok=True)
    for old in parts_dir.glob("*.bam"):
        old.unlink()
    manifest: dict[str, Any] = {
        "schema": 1,
        "complete": False,
        "records": 0,
        "parts": [],
    }
    header: list[bytes] = []
    in_header = True
    writer: subprocess.Popen[bytes] | None = None
    path = parts_dir / "000000.bam"
    count = 0

    def seal() -> None:
        assert writer is not None and writer.stdin is not None
        writer.stdin.close()
        if writer.wait():
            raise WGSExtractError("Staged chunk sorter failed")
        subprocess.run([options.samtools, "quickcheck", "-v", path], check=True)
        _sync_file(path)
        _sync_directory(parts_dir)
        manifest["parts"].append(
            {
                "name": str(path.relative_to(workspace)),
                "bytes": path.stat().st_size,
                "records": count,
                "sha256": _digest(path),
            }
        )
        manifest["records"] += count
        _save(workspace / "parts.json", manifest)
        print(
            f"Staged checkpoint: {manifest['records']:,} records",
            file=sys.stderr,
            flush=True,
        )

    try:
        for line in sys.stdin.buffer:
            if in_header and line.startswith(b"@"):
                header.append(line)
                continue
            in_header = False
            if writer is None:
                path = parts_dir / f"{len(manifest['parts']):06d}.bam"
                writer = subprocess.Popen(
                    [
                        options.samtools,
                        "sort",
                        "-@",
                        options.threads,
                        "-m",
                        options.memory,
                        "-l",
                        "3",
                        "--no-PG",
                        "-T",
                        workspace / "chunk",
                        "-o",
                        path,
                        "-",
                    ],
                    stdin=subprocess.PIPE,
                )
                assert writer.stdin is not None
                writer.stdin.writelines(header)
                count = 0
            assert writer.stdin is not None
            writer.stdin.write(_remember_rg(line))
            count += 1
            if count == options.chunk_records:
                seal()
                writer = None
        if writer is not None:
            seal()
        if not manifest["parts"]:
            raise WGSExtractError("No records reached staged postprocessing")
    finally:
        if writer and writer.poll() is None:
            writer.terminate()
            writer.wait()


def _merge(options: argparse.Namespace) -> None:
    workspace: Path = options.workspace
    manifest_path = workspace / "parts.json"
    manifest = json.loads(manifest_path.read_text())
    parts = manifest["parts"]
    for part in parts:
        _part_path(workspace, part["name"])
    referenced = {part["name"] for part in parts}
    for stale in workspace.glob("merge-*.bam"):
        if stale.name not in referenced:
            stale.unlink()
    fan_in = _FAN_IN
    while len(parts) > fan_in:
        replacement = []
        for start in range(0, len(parts), fan_in):
            group = parts[start : start + fan_in]
            target = workspace / f"merge-{uuid.uuid4().hex}.bam"
            listing = workspace / "merge.list"
            listing.write_text(
                "".join(str(workspace / p["name"]) + "\n" for p in group),
                encoding="utf-8",
                newline="\n",
            )
            subprocess.run(
                [
                    options.samtools,
                    "merge",
                    "-c",
                    "-p",
                    "--no-PG",
                    "-@",
                    options.compression_threads,
                    "-l",
                    "3",
                    "-o",
                    target,
                    "-b",
                    listing,
                ],
                check=True,
            )
            _sync_file(target)
            part = {
                "name": target.name,
                "bytes": target.stat().st_size,
                "records": sum(p["records"] for p in group),
                "sha256": _digest(target),
            }
            replacement.append(part)
            manifest["parts"] = [*replacement, *parts[start + len(group) :]]
            _save(manifest_path, manifest)
            for old in group:
                (workspace / old["name"]).unlink()
        parts = replacement
    listing = workspace / "merge.list"
    listing.write_text(
        "".join(str(workspace / p["name"]) + "\n" for p in parts),
        encoding="utf-8",
        newline="\n",
    )
    merger = subprocess.Popen(
        [
            options.samtools,
            "merge",
            "-c",
            "-p",
            "--no-PG",
            "-@",
            options.threads,
            "-O",
            "SAM",
            "-o",
            "-",
            "-b",
            listing,
        ],
        stdout=subprocess.PIPE,
    )
    encoding = (
        ["-O", "BAM,level=3"]
        if options.format == "BAM"
        else [
            "-O",
            "CRAM",
            "--reference",
            options.reference,
            *output_profile_options(options.output_profile),
        ]
    )
    encoder = subprocess.Popen(
        [
            options.samtools,
            "view",
            "--no-PG",
            "-@",
            options.compression_threads,
            *encoding,
            "--write-index",
            "-o",
            indexed_output(options.output),
            "-",
        ],
        stdin=subprocess.PIPE,
    )
    assert merger.stdout is not None and encoder.stdin is not None
    in_header = True
    try:
        for line in merger.stdout:
            if in_header and line.startswith(b"@"):
                encoder.stdin.write(line)
                continue
            in_header = False
            encoder.stdin.write(_restore_rg(line))
        encoder.stdin.close()
        if encoder.wait() or merger.wait():
            raise WGSExtractError("Staged merge/encoding failed")
    finally:
        for process in (encoder, merger):
            if process.poll() is None:
                process.terminate()
                process.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("mark", "merge"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--samtools", required=True)
    parser.add_argument("--memory", default="2G")
    parser.add_argument("--threads", default="1")
    parser.add_argument("--compression-threads")
    parser.add_argument("--chunk-records", type=int, default=2_000_000)
    parser.add_argument("--output")
    parser.add_argument("--format", choices=("BAM", "CRAM"), default="BAM")
    parser.add_argument("--reference")
    parser.add_argument("--output-profile", choices=OUTPUT_PROFILES, default="default")
    options = parser.parse_args()
    options.compression_threads = options.compression_threads or options.threads

    def interrupted(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if options.action == "mark":
        _mark(options)
    else:
        _merge(options)


if __name__ == "__main__":
    main()
