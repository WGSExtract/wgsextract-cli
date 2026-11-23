"""Cancellable HTSlib and fastVEP tool execution for ACMG data preparation."""

import gzip
import hashlib
import logging
import subprocess
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wgsextract_cli.core.acmg_regions import flags_at_sites, region_name
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.download_progress import CancelEvent, format_bytes
from wgsextract_cli.core.ref_library.segmented import user_agent
from wgsextract_cli.core.utils import WGSExtractError, popen, wait_with_cancel

from ._acmg_data_plan import GNOMAD_KEEP


def run_tool(command: list[str], cancel: CancelEvent | None) -> str:
    """Run a tool, cancellably: its output, or its failure."""
    with tempfile.TemporaryFile() as log:
        process = popen(command, stdout=log, stderr=subprocess.STDOUT)
        ok = wait_with_cancel(process, cancel)
        log.seek(0)
        output = log.read().decode(errors="replace")
    if not ok:
        raise WGSExtractError(
            "Cancelled."
            if cancel and cancel.is_set()
            else f"{Path(command[0]).name} {command[1]} failed "
            f"(exit {process.returncode}): {output.strip()[-1500:]}"
        )
    return output


def add_region_flags(
    vcf: Path,
    folder: Path,
    tracks: dict[str, str],
    chromosome: str,
    cancel: CancelEvent | None,
) -> Path:
    """Mark gnomAD loci (the VCF POS, not the span of REF)."""
    regions = {}
    for flag in tracks:
        text = run_tool(
            [
                get_tool_path("tabix") or "tabix",
                str(folder / region_name(flag)),
                chromosome,
            ],
            cancel,
        )
        regions[flag] = [
            (int(start), int(end))
            for _, start, end in (line.split("\t") for line in text.splitlines())
        ]
    annotated = vcf.with_name(f"{vcf.stem}.regions.vcf.gz")
    with (
        gzip.open(vcf, "rb") as source,
        gzip.open(annotated, "wb", compresslevel=3) as out,
    ):
        out.writelines(flags_at_sites(source, regions, cancel))
    vcf.unlink()
    return annotated


def stream_reduced(
    url: str, size: int, dest: Path, cancel: CancelEvent | None, attempts: int = 6
) -> str:
    """Stream a gnomAD VCF through bcftools into ``dest``, keeping only
    ``GNOMAD_KEEP``. A dropped connection resumes at the byte it stopped at, so
    bcftools reads the file whole; returns the MD5 of what was streamed."""
    partial = dest.with_name(dest.name + ".partial")
    command = [
        "bcftools", "annotate", "--no-version", "-x", f"^{GNOMAD_KEEP}",
        "-Oz", "-o", str(partial), "-",
    ]  # fmt: skip
    digest = hashlib.md5(usedforsecurity=False)
    done = failures = 0
    with tempfile.TemporaryFile() as err:
        process = popen(command, stdin=subprocess.PIPE, stderr=err)
        assert process.stdin is not None
        sink = process.stdin
        try:
            while True:
                headers = {"User-Agent": user_agent(url)}
                if done:
                    headers["Range"] = f"bytes={done}-"
                try:
                    with urlopen(Request(url, headers=headers), timeout=120) as r:
                        if done and r.status != 206:
                            raise WGSExtractError(
                                f"{url} ignored a byte-range request, so its "
                                "stream can't resume."
                            )
                        while chunk := r.read(1 << 20):
                            if cancel and cancel.is_set():
                                raise WGSExtractError("Cancelled.")
                            sink.write(chunk)
                            digest.update(chunk)
                            done += len(chunk)
                    if done < size:
                        raise ConnectionError(f"the server sent {done} of {size} bytes")
                    break
                except BrokenPipeError:
                    break  # bcftools stopped: its exit status and stderr say why
                except HTTPError as e:
                    if e.code != 429 and e.code < 500:
                        raise WGSExtractError(f"Streaming {url} failed: {e}") from e
                    failures = _retry(url, done, size, e, failures, attempts)
                except (OSError, HTTPException) as e:
                    failures = _retry(url, done, size, e, failures, attempts)
        except BaseException:
            process.kill()
            process.wait()
            partial.unlink(missing_ok=True)
            raise
        try:
            sink.close()
        except BrokenPipeError:
            pass
        ok = wait_with_cancel(process, cancel)
        err.seek(0)
        detail = err.read().decode(errors="replace").strip()
    if not ok or done != size:
        partial.unlink(missing_ok=True)
        raise WGSExtractError(
            f"Reducing {Path(url).name} failed after {done} of {size} bytes: "
            f"{detail[-800:] or f'bcftools exited with {process.returncode}'}"
        )
    partial.replace(dest)
    return digest.hexdigest()


def _retry(
    url: str, done: int, size: int, error: Exception, failures: int, attempts: int
) -> int:
    failures += 1
    if failures >= attempts:
        raise WGSExtractError(
            f"Streaming {url} failed at byte {done} of {size}: {error}"
        ) from error
    logging.warning(
        "Streaming %s stopped at %s (%s); resuming...",
        Path(url).name,
        format_bytes(done),
        error,
    )
    time.sleep(min(30, 2**failures))
    return failures
