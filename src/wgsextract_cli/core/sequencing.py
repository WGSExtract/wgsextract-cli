"""Shared, bounded platform detection for reads and alignments."""

import gzip
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core import sample_workspace
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.constants import SEQUENCERS
from wgsextract_cli.core.paths import PathInput
from wgsextract_cli.core.remote_input import is_url, remote_file
from wgsextract_cli.core.results import warn
from wgsextract_cli.core.ultima import read_groups
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command

_READ_GROUPS = "read-group PL tag"
_READ_NAME = "first read name pattern"

_PLATFORMS = {
    "ONT": "ont",
    "PACBIO": "pacbio",
    "ULTIMA": "ultima",
    "ILLUMINA": "illumina",
    "DNBSEQ": "dnbseq",
    "BGISEQ": "dnbseq",
}


def platform_from_metadata(
    header: str = "", qname: str | None = None, sequencer: str | None = None
) -> str | None:
    """Read groups take precedence; mixed platforms must not select one model."""
    platforms = read_group_platforms(header)
    if platforms:
        known = len(platforms) == 1 and platforms[0] in _PLATFORMS.values()
        return platforms[0] if known else None
    names = [sequencer] if sequencer else []
    if qname:
        names += [
            name
            for name, pattern in SEQUENCERS.items()
            if name != "Unknown" and re.search(pattern, qname)
        ]
    for name in names:
        if not name:
            continue
        if name.startswith("Oxford Nanopore"):
            return "ont"
        if name.startswith("PacBio"):
            return "pacbio"
        if name.startswith("Ultima"):
            return "ultima"
        if name.startswith("MGI"):
            return "dnbseq"
        if name.startswith(("Illumina", "Solexa")):
            return "illumina"
    return None


def read_group_platforms(header: str) -> list[str]:
    """The distinct platforms the read groups' PL tags name (DNBSEQ and BGISEQ
    are one), sorted; a PL this module doesn't know is kept, lower-cased."""
    return sorted(
        {
            _PLATFORMS.get(pl.upper(), pl.lower())
            for group in read_groups(header)
            if (pl := group.get("PL"))
        }
    )


@lru_cache(maxsize=128)
def _header_platforms(
    path: str, reference: str | None, size: int, mtime: int, ctime: int
) -> tuple[str, ...]:
    """read_group_platforms of an alignment's header, read once per file
    revision (a URL's is keyed by its HEAD)."""
    return tuple(read_group_platforms(get_bam_header(path, reference)))


def _alignment_platforms(reads: PathInput, reference: PathInput | None) -> list[str]:
    ref = str(reference) if reference else None
    if is_url(reads):
        remote = remote_file(os.fspath(reads))
        key = (os.fspath(reads), ref, remote.size, hash(remote.fingerprint), 0)
    else:
        stat = Path(reads).stat()
        key = (
            str(Path(reads).resolve()), ref, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns,
        )  # fmt: skip
    return list(_header_platforms(*key))


def mixed_platforms(
    reads: PathInput | None, reference: PathInput | None = None
) -> list[str]:
    """The platforms of a BAM/CRAM/SAM whose read groups name more than one
    (such as ONT and Illumina reads merged), else []."""
    if reads is None or not str(reads).lower().endswith((".bam", ".cram", ".sam")):
        return []
    if not is_url(reads) and not Path(reads).is_file():
        return []
    platforms = _alignment_platforms(reads, reference)
    return platforms if len(platforms) > 1 else []


def mixed_platforms_message(reads: PathInput, platforms: list[str]) -> str:
    """What a mixed-platform input is, for warnings and errors to build on."""
    return (
        f"{reads} mixes reads from several sequencing platforms "
        f"({', '.join(platforms)}) in its read groups"
    )


def refuse_mixed_platforms(
    reads: PathInput | None, reference: PathInput | None, choice: str
) -> None:
    """Refuse a mixed-platform input where the platform picks a model or preset
    that would silently degrade the other platform's reads; ``choice`` names
    the option that picks one explicitly."""
    if reads is not None and (platforms := mixed_platforms(reads, reference)):
        raise UsageError(
            f"{mixed_platforms_message(reads, platforms)}, and this command's defaults "
            f"depend on the platform. Pass {choice} to choose explicitly, or split "
            "the reads by read group (`samtools split`) and run each platform "
            "separately."
        )


def warn_mixed_platforms(
    reads: PathInput | None, reference: PathInput | None, consequence: str
) -> list[str]:
    """Warn (on the result manifest too) about a mixed-platform input where
    that is harmless, saying what this command does with it; returns its
    platforms, or [] when it isn't mixed."""
    if reads is None or not (platforms := mixed_platforms(reads, reference)):
        return []
    warn(
        "mixed_platforms",
        f"{mixed_platforms_message(reads, platforms)}; {consequence}",
        platforms=",".join(platforms),
    )
    return platforms


def input_platform(
    reads: PathInput | None,
    reference: PathInput | None = None,
    *,
    strict: bool = False,
) -> str | None:
    """The platform of a reads file: the user's override or the recorded value
    in its sample workspace, else detected (and recorded) from the read groups
    or the first read name. An override is still checked against that cheap
    detection, since it picks the aligner presets and calling models. Detection is cached per file revision, not just
    path (outputs can be replaced in place).

    With ``strict``, an undecodable CRAM read-name probe is an error rather than
    "unknown", so alignment never silently falls back to short-read defaults,
    and so is a BAM/CRAM whose read groups mix platforms.
    """
    if reads is None:
        return None
    if is_url(reads):
        if strict:
            refuse_mixed_platforms(reads, reference, "--platform")
        # A remote file has no sample workspace; its HEAD keys the cache.
        remote = remote_file(os.fspath(reads))
        return _input_platform(
            os.fspath(reads),
            str(reference) if reference else None,
            remote.size,
            hash(remote.fingerprint),
            0,
            strict,
        )[0]
    # With an override, an undecodable probe only skips the check of it
    fact = sample_workspace.known("platform", reads)
    strict = strict and (fact is None or fact.source != "override")
    if strict:
        refuse_mixed_platforms(reads, reference, "--platform")
    platform: str | None = sample_workspace.resolve(
        "platform", reads, lambda: _detect_platform(reads, reference, strict)
    )
    return platform


def _detect_platform(
    reads: PathInput, reference: PathInput | None, strict: bool
) -> tuple[str | None, str | None]:
    path = Path(reads)
    try:
        stat = path.stat()
    except OSError:
        return None, None
    platform, method = _input_platform(
        str(path.resolve()),
        str(reference) if reference else None,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        strict,
    )
    if platform and method:
        sample_workspace.record(
            "platform",
            reads,
            platform,
            method=method,
            confidence="high" if method == _READ_GROUPS else "medium",
        )
    return platform, method


@lru_cache(maxsize=128)
def _input_platform(
    path: str,
    reference: str | None,
    size: int,
    mtime: int,
    ctime: int,
    strict: bool = False,
) -> tuple[str | None, str | None]:
    """(platform, how it was found)."""
    if path.lower().endswith((".bam", ".cram", ".sam")):
        if platforms := _header_platforms(path, reference, size, mtime, ctime):
            known = len(platforms) == 1 and platforms[0] in _PLATFORMS.values()
            return platforms[0] if known else None, _READ_GROUPS
        try:
            result = run_command(
                [
                    "samtools",
                    "head",
                    "-h",
                    "0",
                    "-n",
                    "1",
                    *(["--reference", reference] if reference else []),
                    path,
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise subprocess.CalledProcessError(
                    result.returncode, result.args, result.stdout, result.stderr
                )
            qname = result.stdout.split("\t", 1)[0].strip()
        except (OSError, subprocess.SubprocessError) as error:
            if strict and path.lower().endswith(".cram"):
                raise WGSExtractError(
                    "Unable to probe CRAM read names for platform detection; "
                    "ONT cannot be excluded. Supply the original decoding FASTA "
                    "(--input-ref for realign); refusing to silently choose "
                    "paired short-read defaults."
                ) from error
            return None, None
        return platform_from_metadata(qname=qname), _READ_NAME
    if path.lower().endswith(
        (".fq", ".fastq", ".fq.gz", ".fastq.gz", ".fq.bgz", ".fastq.bgz")
    ):
        opener = gzip.open if path.lower().endswith((".gz", ".bgz")) else open
        try:
            with opener(path, "rt") as handle:
                line = handle.readline(1024 * 1024)
        except (OSError, EOFError, UnicodeDecodeError):
            return None, None
        if line.startswith("@") and (fields := line[1:].split()):
            return platform_from_metadata(qname=fields[0]), _READ_NAME
    return None, None
