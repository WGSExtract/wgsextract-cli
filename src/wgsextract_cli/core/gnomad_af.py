"""The gnomAD v4.1 allele-frequency table, streamed from its joint VCFs."""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, wait
from contextlib import suppress
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import IO
from urllib.request import Request, urlopen

from wgsextract_cli.core.atomic_output import publish_output_set
from wgsextract_cli.core.cancellable_http import cancellable_http
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.download_progress import (
    CancelEvent,
    DownloadCancelled,
    ProgressCallback,
    format_bytes,
)
from wgsextract_cli.core.execution_context import (
    ContextThreadPoolExecutor,
    context_thread,
)
from wgsextract_cli.core.network_policy import require_network
from wgsextract_cli.core.process_registry import terminate_trees
from wgsextract_cli.core.ref_library.layout import download_path
from wgsextract_cli.core.resource_mirrors import mirror_chunks, mirror_for
from wgsextract_cli.core.utils import (
    WGSExtractError,
    popen,
    run_command,
    wait_with_cancel,
)

# gnomAD v4.1 joint (exomes + genomes) sites are GRCh38-only and ~900 GB of
# per-chromosome VCFs with ~660 INFO fields, so `ref gnomad --release 4.1`
# streams them into a compact frequency and homozygote table instead of storing them.
GNOMAD_V4_URL = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/vcf/joint/"
    "gnomad.joint.v4.1.sites.{contig}.vcf.bgz"
)
GNOMAD_V4_CONTIGS = [f"chr{c}" for c in (*range(1, 23), "X", "Y")]
GNOMAD_AF_TABLE = "gnomad_hg38.tsv.gz"
GNOMAD_V4_REBUILD = "wgsextract ref gnomad --release 4.1 --download"
_TABLE_SCHEMA = 3
_JOINT_HEADER = "## gnomAD v4.1 joint exomes+genomes,"
_SCHEMA_MARKER = f"; table schema {_TABLE_SCHEMA}"
_GNOMAD_V4_FIELDS = ("AF_joint", "AF_grpmax_joint", "nhomalt_joint")
_GNOMAD_V4_COLUMNS = ("#CHROM", "POS", "REF", "ALT", "AF", *_GNOMAD_V4_FIELDS[1:])
# Sites gnomAD itself filtered out count as absent, so rare-variant filtering
# keeps the user's call rather than trusting an artifact's frequency.
_GNOMAD_V4_QUERY = (
    "-i",
    'FILTER="PASS" && AF_joint>0',
    "-u",  # optional annotations may not be defined in the source VCF
    "-f",
    "%CHROM\t%POS\t%REF\t%ALT\t"
    + "\t".join(f"%INFO/{f}" for f in _GNOMAD_V4_FIELDS)
    + "\n",
)
_GNOMAD_V4_HEADER = (
    f"{_JOINT_HEADER} PASS alleles with AF_joint > 0{_SCHEMA_MARKER}\n"
    + "\t".join(_GNOMAD_V4_COLUMNS)
    + "\n"
)


@dataclass(frozen=True)
class GnomadTableHeader:
    columns: tuple[str, ...]
    joint: bool
    has_current_schema: bool

    @property
    def is_current(self) -> bool:
        return self.has_current_schema and self.columns == _GNOMAD_V4_COLUMNS


def gnomad_table_header(path: str) -> GnomadTableHeader:
    """Read the column names and joint v4.1 build metadata."""
    joint = has_current_schema = False
    with gzip.open(path, "rt", encoding="utf-8") as table:
        for line in table:
            line = line.rstrip("\r\n")
            if line.startswith("#CHROM\t"):
                return GnomadTableHeader(
                    tuple(line.split("\t")), joint, has_current_schema
                )
            if not line.startswith("#"):
                break
            if line.startswith(_JOINT_HEADER):
                joint = True
                has_current_schema |= line.endswith(_SCHEMA_MARKER)
    return GnomadTableHeader((), joint, has_current_schema)


def _is_current_table(path: str) -> bool:
    try:
        return gnomad_table_header(path).is_current
    except (OSError, EOFError, UnicodeError):
        return False


def _index_table(path: str) -> None:
    run_command(["tabix", "-f", "-s", "1", "-b", "2", "-e", "2", path])


def _remote_size(url: str, cancel_event: CancelEvent | None) -> int:
    if entry := mirror_for(url):
        return int(entry["size"])
    require_network("gnomAD contig size lookup", large=False)
    request = Request(url, method="HEAD")
    response_cm = (
        cancellable_http(request, 60, cancel_event)
        if cancel_event is not None
        else urlopen(request, timeout=60)
    )
    with response_cm as response:
        return int(response.headers.get("Content-Length") or 0)


def _start_stages(
    out: IO[bytes], err: IO[bytes]
) -> tuple[subprocess.Popen[bytes], subprocess.Popen[bytes], subprocess.Popen[bytes]]:
    # Split Number=A INFO values before applying the allele-frequency filter.
    stages = (
        ["bcftools", "norm", "-m", "-any", "-Ou", "-"],
        ["bcftools", "query", *_GNOMAD_V4_QUERY, "-"],
        ["bgzip", "-c"],
    )
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for i, command in enumerate(stages):
            upstream = processes[-1].stdout if processes else None
            try:
                processes.append(
                    popen(
                        command,
                        stdin=upstream if upstream is not None else subprocess.PIPE,
                        stdout=out if i == len(stages) - 1 else subprocess.PIPE,
                        stderr=err,
                    )
                )
            finally:
                if upstream is not None:
                    upstream.close()
    except BaseException:
        if processes and processes[0].stdin is not None:
            processes[0].stdin.close()
        terminate_trees([proc for proc in processes if proc.poll() is None])
        for proc in processes:
            if proc.stdout is not None:
                proc.stdout.close()
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=2)
        raise
    split, query, compress = processes
    return split, query, compress


def _stream_gnomad_contig(
    url: str,
    part: str,
    cancel_event: CancelEvent | None,
    on_bytes: Callable[[int], None],
    *,
    use_mirrors: bool = True,
) -> str | None:
    """Write one contig's AF rows to part (BGZF); return an error message or None.

    The download is fed to bcftools here (not opened by htslib) so progress is
    exact, and so a cut-off download fails instead of leaving a short table.
    """
    require_network("gnomAD allele-frequency contig")
    partial = part + ".partial"
    mirror = mirror_for(url) if use_mirrors else None
    reported = 0
    download_error: list[str] = []

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    with (
        open(partial, "wb") as out,
        tempfile.TemporaryFile(dir=Path(part).parent) as err,
    ):
        split, query, compress = _start_stages(out, err)
        assert split.stdin is not None
        sink = split.stdin

        def feed() -> None:
            nonlocal reported
            try:
                if cancelled():
                    return
                if mirror:
                    staging = Path(part + ".mirror")
                    for chunk in mirror_chunks(mirror, staging, cancel_event):
                        if cancelled():
                            return
                        sink.write(chunk)
                        on_bytes(len(chunk))
                        reported += len(chunk)
                    shutil.rmtree(staging)
                else:
                    pinned = mirror_for(url)
                    digest = hashlib.sha256()
                    received = 0
                    response_cm = (
                        cancellable_http(url, 120, cancel_event)
                        if cancel_event is not None
                        else urlopen(url, timeout=120)
                    )
                    with response_cm as response:
                        while not cancelled() and (chunk := response.read(1 << 20)):
                            if cancelled():
                                return
                            sink.write(chunk)
                            on_bytes(len(chunk))
                            if pinned:
                                digest.update(chunk)
                            received += len(chunk)
                    if cancelled():
                        return
                    if pinned:
                        if (
                            digest.hexdigest() != pinned["sha256"]
                            or received != pinned["size"]
                        ):
                            raise OSError(
                                "upstream differs from the pinned gnomAD snapshot"
                            )
            except BrokenPipeError:
                pass  # bcftools exited; its status says why
            except DownloadCancelled:
                pass
            except (OSError, HTTPException) as e:
                if not cancelled():
                    download_error.append(f"download failed: {e}")
                    split.kill()
            finally:
                try:
                    sink.close()
                except OSError:
                    pass

        feeder = context_thread(target=feed, daemon=True)
        feeder.start()
        normalized = wait_with_cancel(split, cancel_event)
        queried = wait_with_cancel(query, cancel_event)
        while feeder.is_alive() and not cancelled():
            feeder.join(timeout=0.1)
        if cancelled():
            # DNS or connection setup may not yet have a socket to shut down.
            feeder.join(timeout=1)
        compressed = wait_with_cancel(compress, cancel_event)
        ok = (
            normalized
            and queried
            and compressed
            and not download_error
            and not cancelled()
        )
        err.seek(0)
        detail = err.read().decode(errors="replace").strip().splitlines()[-3:]
    # Closed first: Windows can't rename or delete an open file.
    if ok:
        Path(partial).replace(part)
        return None
    Path(partial).unlink()
    if cancelled():
        return "cancelled"
    if mirror:
        logging.warning(
            "MIRROR_FALLBACK: gnomAD mirror failed; discarding partial AF rows "
            "and restarting %s from upstream.",
            url,
        )
        on_bytes(-reported)
        return _stream_gnomad_contig(
            url, part, cancel_event, on_bytes, use_mirrors=False
        )
    return (
        "; ".join(download_error + detail) or f"bcftools exited with {query.returncode}"
    )


def build_gnomad_af(
    reflib_dir: str,
    workers: int = 6,
    cancel_event: CancelEvent | None = None,
    progress_callback: ProgressCallback | None = None,
    attempts: int = 3,
    *,
    download: bool = False,
) -> bool:
    """
    Build <library>/downloads/gnomad_hg38.tsv.gz from the gnomAD v4.1 joint VCFs.

    Contigs stream in parallel (the build is network-bound) into resumable
    per-contig parts, which are then concatenated (BGZF allows that) and indexed.
    """
    dest = str(download_path(reflib_dir, GNOMAD_AF_TABLE))
    if _is_current_table(dest):
        if not Path(dest + ".tbi").is_file():
            verify_dependencies(["tabix"])
            _index_table(dest)
        logging.info(f"{GNOMAD_AF_TABLE} is already installed.")
        return True
    if not download:
        problem = (
            "needs rebuilding to use the current per-allele format"
            if Path(dest).is_file()
            else "is not installed"
        )
        raise WGSExtractError(
            f"gnomAD v4.1 table ({dest}) {problem}. "
            f"To stream the public VCFs, run: {GNOMAD_V4_REBUILD}"
        )
    verify_dependencies(["bcftools", "bgzip", "tabix"])
    # Older parts may have only five columns or have unsplit alleles.
    parts_dir = str(download_path(reflib_dir, f".gnomad_hg38_parts_v{_TABLE_SCHEMA}"))
    if not Path(dest).exists():
        dest = str(Path(parts_dir).parent / GNOMAD_AF_TABLE)
    Path(parts_dir).mkdir(parents=True, exist_ok=True)

    def part_path(contig: str) -> str:
        return str(Path(parts_dir, f"{contig}.tsv.gz"))

    urls = {c: GNOMAD_V4_URL.format(contig=c) for c in GNOMAD_V4_CONTIGS}
    try:
        sizes = {c: _remote_size(url, cancel_event) for c, url in urls.items()}
    except DownloadCancelled:
        return False
    except OSError as e:
        logging.error(f"Could not reach the gnomAD v4.1 bucket: {e}")
        return False
    total = sum(sizes.values())
    resumed = sum(sizes[c] for c in urls if Path(part_path(c)).is_file())
    todo = sorted(
        (c for c in urls if not Path(part_path(c)).is_file()),
        key=sizes.__getitem__,
        reverse=True,  # biggest first, so the last contig to finish is short
    )
    logging.info(
        f"Streaming {len(todo)} gnomAD v4.1 contigs ({format_bytes(total - resumed)} "
        f"of VCF, {workers} at a time) into an allele-frequency table..."
    )

    progress_lock = threading.Lock()
    streamed = dict.fromkeys(todo, 0)

    def stream(contig: str) -> str | None:
        def count(n: int) -> None:
            with progress_lock:
                streamed[contig] += n

        error = None
        for attempt in range(1, attempts + 1):
            if cancel_event and cancel_event.is_set():
                return "cancelled"
            with progress_lock:
                streamed[contig] = 0
            part = part_path(contig)
            try:
                error = _stream_gnomad_contig(urls[contig], part, cancel_event, count)
            except OSError as e:
                Path(part + ".partial").unlink(missing_ok=True)
                error = str(e)
            if error is None:
                return None
            logging.warning(f"gnomAD {contig} attempt {attempt} failed: {error}")
        return error

    def report(note: str) -> None:
        with progress_lock:
            done = resumed + sum(streamed.values())
        rate = (done - resumed) / max(time.monotonic() - start, 1e-9)
        eta = f", about {(total - done) / rate / 60:.0f} min left" if rate else ""
        logging.info(
            f"gnomAD {note}{format_bytes(done)} of {format_bytes(total)} "
            f"at {format_bytes(rate)}/s{eta}"
        )
        if progress_callback:
            progress_callback(done, total, rate)

    start = time.monotonic()
    failed: list[str] = []
    with ContextThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        pending = {pool.submit(stream, c): c for c in todo}
        while pending:
            finished, _ = wait(pending, timeout=60, return_when=FIRST_COMPLETED)
            if not finished:
                report("")
            for future in finished:
                contig = pending.pop(future)
                if future.result() is None:
                    report(f"{contig} done: ")
                else:
                    failed.append(contig)
    if cancel_event and cancel_event.is_set():
        return False
    if failed:
        logging.error(
            f"gnomAD contigs failed: {', '.join(failed)}. Rerun to resume; "
            "finished contigs are kept."
        )
        return False

    partial = dest + ".partial"
    with open(partial, "wb") as out:
        header = popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=out)
        header.communicate(_GNOMAD_V4_HEADER.encode())
        if header.returncode != 0:
            raise WGSExtractError("bgzip failed to write the gnomAD table header.")
        out.flush()
        for contig in GNOMAD_V4_CONTIGS:
            with open(part_path(contig), "rb") as part:
                shutil.copyfileobj(part, out, 8 << 20)
    logging.info(f"Indexing {GNOMAD_AF_TABLE}...")
    partial_index = Path(partial + ".tbi")
    index = Path(dest + ".tbi")
    try:
        _index_table(partial)
        if cancel_event and cancel_event.is_set():
            return False
        publish_output_set({partial_index: index, Path(partial): Path(dest)})
    except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
        logging.error(f"Preparing or publishing {dest} failed: {e}")
        return False
    finally:
        Path(partial).unlink(missing_ok=True)
        partial_index.unlink(missing_ok=True)
    shutil.rmtree(parts_dir)
    return True
