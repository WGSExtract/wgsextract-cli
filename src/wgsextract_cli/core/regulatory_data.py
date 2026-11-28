"""Installation and provenance for the offline SCREEN cCRE BED."""

import gzip
import hashlib
import json
import logging
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath, PureWindowsPath
from tempfile import TemporaryDirectory

from wgsextract_cli.core import read_scope
from wgsextract_cli.core.atomic_output import publish_output_set
from wgsextract_cli.core.download_progress import CancelEvent
from wgsextract_cli.core.ref_library.assets import resolve_reference_library
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.utils import WGSExtractError, popen, run_command

LABEL = "ENCODE SCREEN cCRE Registry v4"
BUILD = "hg38"
URL = "https://downloads.wenglab.org/Registry-V4/GRCh38-cCREs.bed"
SHA256 = "a292aea64edea7efbef275140b78116efdef1bea5b729dc9e5513421dc868dda"
TERMS = "https://www.genome.ucsc.edu/ENCODE/terms.html"
_TABLE = "screen-v4-hg38.bed.gz"
_TOKEN = re.compile(r"[A-Za-z0-9_.-]+")


def path(reflib: str | None) -> Path:
    return Path(resolve_reference_library(reflib), "downloads", _TABLE)


def install_command(table: Path) -> str:
    return shlex.join(
        [
            "wgsextract",
            "ref",
            "regulatory",
            "--source",
            "screen",
            "--build",
            BUILD,
            "--ref",
            str(table.parent.parent),
            "--download",
        ]
    )


def missing(table: Path) -> str:
    return (
        f"{LABEL} (GRCh38) is missing or damaged. "
        f"To fix this, run: {install_command(table)}"
    )


def index(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".tbi")


def marker(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def resource_paths(table: PurePath) -> Iterator[tuple[str, Path]]:
    for path in (
        table.parent,
        table,
        table.with_suffix(table.suffix + ".tbi"),
        table.with_suffix(table.suffix + ".json"),
    ):
        yield "ref", Path(str(path))


def guarded_paths(ref: Path | None) -> Iterator[tuple[str, Path]]:
    from wgsextract_cli.core.ref_library.resolver import library_roots

    if ref is not None:
        # The guard consumes this before library_roots can inspect a FASTA.
        yield "ref", ref
        windows = PureWindowsPath(ref)
        if not ref.is_absolute() and windows.drive:
            # Keep drive and separator semantics when the guard uses a
            # different path flavour from the host.
            yield from resource_paths(windows / "downloads" / _TABLE)
            return
    yield from resource_paths(path(library_roots(ref).root))


def _allowed(table: Path) -> bool:
    return all(read_scope.may_follow(file) for _, file in resource_paths(table))


@contextmanager
def snapshot(table: Path, workdir: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    """Keep immutable, verified files while tabix opens the track for each contig."""
    if not _allowed(table):
        raise WGSExtractError(
            f"SCREEN track is outside the allowed reference folders: {table}"
        )
    with TemporaryDirectory(prefix=".screen-track-", dir=workdir) as stage:
        local = Path(stage, table.name)
        try:
            for src, dst in (
                (table, local),
                (index(table), index(local)),
                (marker(table), marker(local)),
            ):
                shutil.copyfile(src, dst)
        except FileNotFoundError as exc:
            raise WGSExtractError(missing(table)) from exc
        except OSError as exc:
            raise WGSExtractError(f"Could not stage the SCREEN track: {exc}") from exc
        source = provenance(local, verify=True)
        if source is None:
            raise WGSExtractError(missing(table))
        yield local, source


def _sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provenance(table: Path, *, verify: bool = False) -> dict[str, str] | None:
    """Return provenance only for a complete (and optionally verified) install."""
    if not _allowed(table):
        return None
    try:
        raw = json.loads(marker(table).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        data = {
            k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)
        }
        if not (
            data["release"] == LABEL
            and data["build"] == BUILD
            and _is_source(data)
            and table.is_file()
            and index(table).is_file()
        ):
            return None
        if verify and (
            data["bed_sha256"] != _sha256(table)
            or data["index_sha256"] != _sha256(index(table))
        ):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data


def _is_source(data: dict[str, str]) -> bool:
    return (
        data["origin"] == "upstream"
        and data["source_sha256"] == SHA256
        and data["source_url"] == URL
    ) or (
        data["origin"] == "local-import"
        and bool(re.fullmatch(r"[a-f0-9]{64}", data["source_sha256"]))
    )


def _compress_bed(source: Path, table: Path, cancel: CancelEvent | None) -> None:
    opener = gzip.open if source.suffix.lower() in {".gz", ".bgz"} else open
    previous_chrom, previous_start = "", -1
    seen: set[str] = set()
    with (
        opener(source, "rt", encoding="ascii") as stream,
        table.open("wb") as sink,
        popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=sink) as writer,
    ):
        assert writer.stdin is not None
        try:
            for number, line in enumerate(stream, 1):
                if cancel and cancel.is_set():
                    raise WGSExtractError("Cancelled.")
                if not line.strip() or line.startswith("#"):
                    continue
                fields = line.rstrip("\r\n").split("\t")
                try:
                    chrom, start, end, dhs, accession, group = fields
                    lo, hi = int(start), int(end)
                    if not (
                        0 <= lo < hi < 2**29
                        and all(
                            _TOKEN.fullmatch(x) for x in (chrom, dhs, accession, group)
                        )
                        and accession != "."
                        and group != "."
                    ):
                        raise ValueError
                except ValueError as exc:
                    raise WGSExtractError(
                        f"Invalid SCREEN BED row {number}; expected "
                        "CHROM, START, END, DHS_ID, cCRE_ID, CLASS."
                    ) from exc
                if chrom != previous_chrom:
                    if chrom in seen:
                        raise WGSExtractError(
                            f"SCREEN BED contig {chrom} is not grouped."
                        )
                    seen.add(chrom)
                    previous_chrom, previous_start = chrom, -1
                if lo < previous_start:
                    raise WGSExtractError(
                        f"SCREEN BED is not sorted by start at row {number} ({chrom})."
                    )
                previous_start = lo
                writer.stdin.write(
                    f"{chrom}\t{lo}\t{hi}\t{accession}\t{group}\n".encode()
                )
            if not seen:
                raise WGSExtractError("SCREEN BED has no intervals.")
            writer.stdin.close()
            if writer.wait() != 0:
                raise WGSExtractError("bgzip could not compress the SCREEN BED.")
        except BaseException:
            writer.kill()
            raise


def install(
    table: Path,
    source: Path | None,
    *,
    cancel: CancelEvent | None = None,
) -> None:
    """Validate a sorted SCREEN BED and publish its BGZF, index and provenance."""
    if not _allowed(table):
        raise WGSExtractError(
            f"SCREEN track is outside the allowed reference folders: {table}"
        )
    table.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".screen-v4-", dir=table.parent) as stage:
        staged = Path(stage, table.name)
        if source is None:
            source = Path(stage, "GRCh38-cCREs.bed")
            logging.info("Downloading public %s data from %s (129 MB).", LABEL, URL)
            if not download_file(
                URL,
                str(source),
                cancel_event=cancel,
                expected_sha256=SHA256,
                resolve_release_digest=False,
                use_mirrors=False,
            ):
                raise WGSExtractError(
                    f"Could not download and verify {LABEL} (SHA-256). "
                    f"Rerun: {install_command(table)}"
                )
            origin, digest = "upstream", SHA256
        else:
            origin, digest = "local-import", _sha256(source)
        try:
            _compress_bed(source, staged, cancel)
        except (OSError, UnicodeError) as exc:
            raise WGSExtractError(f"Could not prepare SCREEN BED: {exc}") from exc
        run_command(["tabix", "-p", "bed", staged])
        receipt = {
            "release": LABEL,
            "build": BUILD,
            "origin": origin,
            "source_sha256": digest,
            "bed_sha256": _sha256(staged),
            "index_sha256": _sha256(index(staged)),
            "terms": TERMS,
            "credit": "ENCODE Project and the SCREEN Registry",
        }
        if origin == "upstream":
            receipt["source_url"] = URL
        marker(staged).write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        publish_output_set(
            {index(staged): index(table), marker(staged): marker(table), staged: table}
        )
