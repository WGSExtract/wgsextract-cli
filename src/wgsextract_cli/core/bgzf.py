"""BGZF: the blocked gzip that htslib needs for random access."""

from __future__ import annotations

import io
import logging
import subprocess
import threading
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.utils import WGSExtractError, popen, wait_with_cancel

_GZIP_MAGIC = b"\x1f\x8b"


def is_bgzf(path: str) -> bool:
    """
    Whether path starts with a BGZF block: a gzip header whose extra field has
    the BC subfield. Another gzip with an extra field (RAZF, which NIH serves
    hs37d5 as) is not one: htslib can't index it.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[:4] != _GZIP_MAGIC + b"\x08\x04":
                return False
            extra = f.read(int.from_bytes(header[10:12], "little"))
    except OSError as e:
        raise WGSExtractError(f"Failed to inspect gzip header for {path}: {e}") from e
    while len(extra) >= 4:
        length = int.from_bytes(extra[2:4], "little")
        if extra[:2] == b"BC" and length == 2:
            return True
        extra = extra[4 + length :]
    return False


class _GzipMembers(io.RawIOBase):
    """
    Every gzip member's content, ending at the first bytes that aren't one:
    RAZF appends its block index after the gzip data, where gzip.open fails.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._file = open(path, "rb")
        self._inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
        self._pending = b""
        self._done = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: WriteableBuffer, /) -> int:
        while not self._pending and not self._done:
            self._pending = self._inflate()
        view = memoryview(buffer).cast("B")
        size = min(len(view), len(self._pending))
        view[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size

    def _inflate(self) -> bytes:
        if self._inflater.eof:
            rest = self._inflater.unused_data
            rest += self._file.read(max(0, 2 - len(rest)))
            if rest[:2] != _GZIP_MAGIC:
                if rest:
                    logging.info(
                        f"{self._path}: ignoring data after the gzip stream "
                        "(an index, as RAZF appends)."
                    )
                self._done = True
                return b""
            self._inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
            return self._inflater.decompress(rest)
        chunk = self._file.read(1 << 20)
        if not chunk:
            raise EOFError(f"{self._path} ends before its gzip stream does.")
        return self._inflater.decompress(chunk)

    def close(self) -> None:
        self._file.close()
        super().close()


def open_decompressed(path: str) -> IO[bytes]:
    """A gzip file's content (every member), or a single-file zip's."""
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic == b"PK":
        archive = zipfile.ZipFile(path)
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) != 1:
            archive.close()
            raise WGSExtractError(
                f"{path} is a zip of {len(members)} files; expected one FASTA."
            )
        return archive.open(members[0])
    return io.BufferedReader(_GzipMembers(path), 1 << 20)


def _feed_decompressed(
    path: str,
    proc: subprocess.Popen[bytes],
    errors: list[BaseException],
) -> None:
    """Write path's decompressed content to proc's stdin (run in a thread)."""
    assert proc.stdin is not None
    try:
        with open_decompressed(path) as source:
            while chunk := source.read(1 << 20):
                proc.stdin.write(chunk)
    except BrokenPipeError:
        pass  # bgzip stopped: cancelled, or its exit status says why
    except (
        OSError,
        EOFError,
        RuntimeError,
        zlib.error,
        zipfile.BadZipFile,
        WGSExtractError,
    ) as e:
        errors.append(e)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def ensure_bgzf(
    path: str,
    status_callback: Callable[[str], None] | None = None,
    cancel_event: CancelEvent | None = None,
) -> str | None:
    """
    Recompress path to BGZF in place (a non-.gz file becomes path + ".gz").

    Returns the BGZF path, or None if cancelled; raises WGSExtractError on failure.
    """
    if is_bgzf(path):
        logging.info(f"{path} is already in BGZF format.")
        return path
    if cancel_event and cancel_event.is_set():
        return None

    logging.info(f"Recompressing {path} to BGZF format (required for fast access)...")
    if status_callback:
        status_callback("Processing: Recompressing (BGZF)...")

    # By content, not name: some mirrors serve plain FASTA as .fa.gz, and YSEQ
    # serves hg19 as a single-file zip.
    with open(path, "rb") as f:
        magic = f.read(2)
    compressed = magic in (b"\x1f\x8b", b"PK")
    new_path = path if path.endswith(".gz") else path + ".gz"
    tmp_path = path + ".tmp.gz"
    proc: subprocess.Popen[bytes] | None = None
    feed_errors: list[BaseException] = []
    try:
        with open(tmp_path, "wb") as f_out:
            if compressed:
                # Decompress here, not with gzip(1): Windows has no native gzip.
                proc = popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=f_out)
                feeder = threading.Thread(
                    target=_feed_decompressed,
                    args=(path, proc, feed_errors),
                    daemon=True,
                )
                feeder.start()
            else:
                proc = popen(["bgzip", "-c", path], stdout=f_out)
            wait_with_cancel(proc, cancel_event)
            if compressed:
                feeder.join()
            if cancel_event and cancel_event.is_set():
                return None
            if feed_errors:
                raise WGSExtractError(
                    f"Could not decompress {path}: {feed_errors[0]}"
                ) from feed_errors[0]
            if proc.wait() != 0:
                raise WGSExtractError(
                    f"BGZF recompression of {path} failed (bgzip exit {proc.wait()})."
                )
        Path(tmp_path).replace(new_path)
        if new_path != path:
            Path(path).unlink()
    except OSError as e:
        raise WGSExtractError(f"BGZF recompression of {path} failed: {e}") from e
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
    logging.info(f"Recompression to {new_path} complete.")
    return new_path
