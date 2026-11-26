"""Pinned UCSC cytobands, installed explicitly for offline genome viewing."""

import gzip
import re
import shlex
import uuid
from pathlib import Path

from wgsextract_cli.core.annotation_resources import CancelEvent
from wgsextract_cli.core.atomic_output import atomic_write
from wgsextract_cli.core.file_lock import build_lock
from wgsextract_cli.core.network_policy import DownloadRequired
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.ref_library.layout import download_path
from wgsextract_cli.core.utils import WGSExtractError

# UCSC cytoBand database snapshots verified 2026-11-20. Data terms, not the
# UCSC software licence, apply; see docs/reference-genomes.md.
CYTOBAND_SOURCES = {
    "hg19": (
        "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/cytoBand.txt.gz",
        "f9b82309b2bca1eb9d91a5cb2c6aa0528351158e6e20b51d82cca36d01735cba",
    ),
    "hg38": (
        "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/cytoBand.txt.gz",
        "e514e48b0a12a516fd5231b44241a20a282f290bdd6131cadcddc6276ee26082",
    ),
}
STAINS = {"gneg", "gpos25", "gpos50", "gpos75", "gpos100", "acen", "gvar", "stalk"}
PRIMARY_CONTIGS = {f"chr{i}" for i in range(1, 23)} | {"chrX", "chrY"}
MAX_TABLE_BYTES = 1024 * 1024
CHROMOSOME_LENGTHS = {
    "hg19": [
        249250621,
        243199373,
        198022430,
        191154276,
        180915260,
        171115067,
        159138663,
        146364022,
        141213431,
        135534747,
        135006516,
        133851895,
        115169878,
        107349540,
        102531392,
        90354753,
        81195210,
        78077248,
        59128983,
        63025520,
        48129895,
        51304566,
        155270560,
        59373566,
    ],
    "hg38": [
        248956422,
        242193529,
        198295559,
        190214555,
        181538259,
        170805979,
        159345973,
        145138636,
        138394717,
        133797422,
        135086622,
        133275309,
        114364328,
        107043718,
        101991189,
        90338345,
        83257441,
        80373285,
        58617616,
        64444167,
        46709983,
        50818468,
        156040895,
        57227415,
    ],
}


def cytoband_path(reflib: str | Path, build: str) -> Path:
    if build not in CYTOBAND_SOURCES:
        raise WGSExtractError("Cytobands are available for hg19 and hg38 only.")
    return download_path(reflib, f"cytobands_{build}.tsv")


def validate_cytobands(data: bytes, build: str, *, source: bool = False) -> bytes:
    """Validate all rows; retain only primary chromosomes from the UCSC source.

    Coordinates are UCSC's zero-based, half-open intervals, with continuous
    coverage from zero on each chromosome. Source alt/scaffold bands have
    empty names and are validated but are not chromosome ideograms.
    """
    if not data or len(data) > MAX_TABLE_BYTES or not data.endswith(b"\n"):
        raise WGSExtractError("Invalid cytoband table: empty, oversized or truncated.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WGSExtractError("Invalid cytoband table: UTF-8 required.") from exc
    ends: dict[str, int] = {}
    names: set[tuple[str, str]] = set()
    rows: list[str] = []
    for line in text.replace("\r\n", "\n")[:-1].split("\n"):
        columns = line.split("\t")
        if len(columns) != 5:
            raise WGSExtractError("Invalid cytoband table: five columns required.")
        chrom, raw_start, raw_end, name, stain = columns
        if (
            not re.fullmatch(r"chr[A-Za-z0-9_.-]{1,100}", chrom)
            or not re.fullmatch(r"[0-9]{1,10}", raw_start)
            or not re.fullmatch(r"[0-9]{1,10}", raw_end)
            or stain not in STAINS
        ):
            raise WGSExtractError(
                "Invalid cytoband table: contig, coordinates or stain."
            )
        start, end = int(raw_start), int(raw_end)
        primary = chrom in PRIMARY_CONTIGS
        if (
            start != ends.get(chrom, 0)
            or end <= start
            or end > 1_000_000_000
            or (primary and not re.fullmatch(r"[pq][0-9]{1,2}(?:\.[0-9]{1,3})?", name))
            or (not primary and (not source or name != ""))
            or (primary and (chrom, name) in names)
        ):
            raise WGSExtractError(
                "Invalid cytoband table: name, gap or overlapping bounds."
            )
        ends[chrom] = end
        if primary:
            if len(rows) >= 10_000:
                raise WGSExtractError("Invalid cytoband table: too many primary bands.")
            names.add((chrom, name))
            rows.append(line)
    expected = dict(
        zip(
            [f"chr{i}" for i in range(1, 23)] + ["chrX", "chrY"],
            CHROMOSOME_LENGTHS.get(build, []),
            strict=False,
        )
    )
    if not expected or any(
        ends.get(chrom) != length for chrom, length in expected.items()
    ):
        raise WGSExtractError("Invalid cytoband table: incomplete or wrong assembly.")
    return ("\n".join(rows) + "\n").encode("utf-8")


def install_cytobands(
    reflib: str | Path,
    build: str,
    *,
    download: bool = False,
    cancel_event: CancelEvent | None = None,
    repair_command: str | None = None,
) -> tuple[Path, bool]:
    """Return the validated asset and whether this call published it."""
    target = cytoband_path(reflib, build)

    def installed() -> bool:
        try:
            if not target.resolve().is_relative_to(Path(reflib).resolve()):
                return False
            with target.open("rb") as handle:
                validate_cytobands(handle.read(MAX_TABLE_BYTES + 1), build)
            return True
        except (OSError, WGSExtractError):
            return False

    if installed():
        return target, False
    if not download:
        command = repair_command or shlex.join(
            [
                "wgsextract",
                "ref",
                "cytobands",
                "--build",
                build,
                "--ref",
                str(reflib),
                "--download",
            ]
        )
        raise DownloadRequired(
            f"Missing or invalid public reference data: {target.name}. "
            f"Rerun {command} to confirm the UCSC download."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.resolve().is_relative_to(Path(reflib).resolve()):
        raise WGSExtractError("Cytoband resource folder escapes the reference library.")
    with build_lock(str(target) + ".lock"):
        if installed():
            return target, False
        _publish(target, build, cancel_event)
    return target, True


def _publish(target: Path, build: str, cancel_event: CancelEvent | None) -> None:
    # Only the URL-bound partial persists at the conventional archive path.
    # Completed archives live in a newly reserved private directory.
    staging = target.parent / f".cytobands-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    archive = staging / target.with_suffix(".txt.gz").name
    persistent = target.with_suffix(".txt.gz")
    suffixes = (".partial", ".partial.source", ".partial.segments")
    try:
        for suffix in suffixes:
            saved = Path(str(persistent) + suffix)
            if saved.exists():
                saved.replace(str(archive) + suffix)
        url, digest = CYTOBAND_SOURCES[build]
        if not download_file(
            url,
            str(archive),
            cancel_event=cancel_event,
            expected_sha256=digest,
            large=False,
        ):
            raise WGSExtractError(
                f"Failed to download {target.name}; rerun with --download to resume."
            )
        with gzip.open(archive, "rb") as handle:
            data = handle.read(MAX_TABLE_BYTES + 1)
        table = validate_cytobands(data, build, source=True)
        if cancel_event and cancel_event.is_set():
            raise WGSExtractError("Cytoband installation cancelled.")
        atomic_write(target, table, new_mode=0o666)
    except (OSError, EOFError) as exc:
        raise WGSExtractError(f"Invalid cytoband archive for {build}: {exc}") from exc
    finally:
        for suffix in suffixes:
            partial = Path(str(archive) + suffix)
            if partial.exists():
                partial.replace(str(persistent) + suffix)
        archive.unlink(missing_ok=True)
        staging.rmdir()
