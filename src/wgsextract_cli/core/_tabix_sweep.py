"""Native BGZF decompression for an indexed local sweep."""

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.execution_context import context_thread
from wgsextract_cli.core.process_registry import terminate_trees
from wgsextract_cli.core.utils import WGSExtractError, popen


def sweep_local(
    bgzip: str,
    source: BinaryIO,
    skip: int,
    contig: str,
    sites: list[tuple[int, int]],
    work: Path,
    sink: Callable[[list[bytes]], None],
    cancel: CancelEvent | None,
) -> None:
    if cancel and cancel.is_set():
        raise WGSExtractError("Cancelled.")
    stderr = work / "bgzip-sweep.stderr"
    with stderr.open("w+b") as errors:
        proc = popen(
            [bgzip, "-dc"],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        assert proc.stdout is not None
        finished = threading.Event()

        def watch() -> None:
            while not finished.wait(0.1):
                if cancel and cancel.is_set():
                    terminate_trees([proc])
                    return

        watcher = context_thread(target=watch) if cancel else None
        if watcher is not None:
            watcher.start()
        try:
            prefix = contig.encode() + b"\t"
            column = len(prefix)
            batch: list[bytes] = []
            site = 0
            lo, hi = sites[0]
            end = sites[-1][1]
            stopped_at_bound = False
            with proc.stdout as stream:
                if len(stream.read(skip)) != skip:
                    raise WGSExtractError("BGZF input ended before its indexed record.")
                for line in stream:
                    if not line.startswith(prefix):
                        if line.startswith(b"#") or line == b"\n":
                            continue
                        stopped_at_bound = True
                        break
                    pos = int(line[column : line.index(b"\t", column)])
                    if pos > end:
                        stopped_at_bound = True
                        break
                    while pos > hi:
                        site += 1
                        lo, hi = sites[site]
                    if pos >= lo and line.endswith(b"\n"):
                        # Keep original line bytes and match only POS, not END.
                        batch.append(line[:-1])
                        if len(batch) >= 128:
                            sink(batch)
                            batch = []
            status = proc.wait()
            if cancel and cancel.is_set():
                raise WGSExtractError("Cancelled.")
            if status and not stopped_at_bound:
                errors.seek(0)
                detail = errors.read(4096).decode(errors="replace").strip()
                raise WGSExtractError(
                    f"Decompressing the indexed sweep failed: {detail}"
                )
            if batch:
                sink(batch)
        finally:
            finished.set()
            if proc.poll() is None:
                terminate_trees([proc])
            if watcher is not None:
                watcher.join()
    stderr.unlink()
