"""Streaming per-ALT overlap against the indexed SCREEN BED."""

import re
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from wgsextract_cli.core.alignment.metadata import (
    vcf_detection_header,
    vcf_metadata_build_hint,
)
from wgsextract_cli.core.builds import build_from_path, is_hg38_build
from wgsextract_cli.core.regulatory_data import primary_aliases
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command
from wgsextract_cli.core.variant_files import chromosome_aliases
from wgsextract_cli.core.vcf_header import attributes

CCRE_CLASS = "CCRE_CLASS"
CCRE_ID = "CCRE_ID"
_TAGS = (CCRE_CLASS, CCRE_ID)
_INFO_HEADERS = (
    '##INFO=<ID=CCRE_CLASS,Number=A,Type=String,Description="SCREEN cCRE classes per ALT; multiple values separated by |; research overlap only">',
    '##INFO=<ID=CCRE_ID,Number=A,Type=String,Description="SCREEN cCRE accessions per ALT; multiple values separated by |; research overlap only">',
)
_SEQUENCE = re.compile(r"[ACGTN]+", re.IGNORECASE | re.ASCII)
Interval = tuple[int, int, str, str]


def require_grch38(vcf: str) -> None:
    aliases = primary_aliases()
    accessions = {alias.partition(".")[0] for alias in aliases if "." in alias}
    confirmed = False
    for line in vcf_detection_header(vcf).splitlines():
        if line.startswith("##contig=<") and line.endswith(">"):
            fields = attributes(line[10:-1])
            name = fields.get("ID", "").strip('"')
            key = name.upper()
            chrom = aliases.get(key)
            length = fields.get("length", "").strip('"')
            if chrom and length.isdecimal():
                if int(length) != chrom.length:
                    raise _wrong_build(
                        f"{name} has length {int(length):,}, expected {chrom.length:,}"
                    )
                confirmed |= chrom.name != "chrM"
            if chrom and "." in key and chrom.name != "chrM":
                confirmed = True
            if not chrom and key.partition(".")[0] in accessions:
                raise _wrong_build(f"incompatible primary accession {name}")
            declared = build_from_path(fields.get("assembly", ""))
        else:
            declared = vcf_metadata_build_hint(line)
        if declared:
            if not is_hg38_build(declared):
                raise _wrong_build(f"the header declares {declared}")
            confirmed = True
    if not confirmed:
        raise _wrong_build("no build in the header")


def _wrong_build(reason: str) -> UsageError:
    return UsageError(
        f"SCREEN cCRE annotation requires a GRCh38 VCF; {reason}. "
        "No liftover is performed."
    )


def _changed_span(pos: int, ref: str, alt: str) -> tuple[int, int] | None:
    if not _SEQUENCE.fullmatch(alt):
        return None
    alt = alt.upper()
    if ref == alt:
        return None
    prefix = 0
    limit = min(len(ref), len(alt))
    while prefix < limit and ref[prefix] == alt[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and ref[-1 - suffix] == alt[-1 - suffix]:
        suffix += 1
    lo, hi = pos + prefix, pos + len(ref) - suffix
    # An insertion overlaps the base immediately to the right of its breakpoint.
    return lo, max(lo + 1, hi)


@contextmanager
def _intervals(table: Path, chrom: str, start: int) -> Iterator[Iterator[Interval]]:
    with popen(
        ["tabix", table, f"{chrom}:{start + 1}-"],
        stdout=subprocess.PIPE,
        text=True,
    ) as query:
        stdout = query.stdout
        assert stdout is not None

        def rows() -> Iterator[Interval]:
            for line in stdout:
                _, lo, hi, accession, group = line.rstrip("\n").split("\t")
                yield int(lo), int(hi), accession, group
            if query.wait() != 0:
                raise WGSExtractError(
                    f"Could not read SCREEN cCRE intervals on {chrom}."
                )

        try:
            yield rows()
        finally:
            # A sparse input need not consume the remainder of the chromosome.
            if query.poll() is None:
                query.terminate()


class _Sweep:
    def __init__(self, rows: Iterator[Interval]) -> None:
        self.rows = rows
        self.next = next(rows, None)
        self.active: list[Interval] = []

    def at(self, pos: int, end: int) -> list[Interval]:
        # Retire at the record POS, not at an ALT's possibly later change.
        self.active = [row for row in self.active if row[1] > pos]
        while self.next is not None and self.next[0] < end:
            if self.next[1] > pos:
                self.active.append(self.next)
            self.next = next(self.rows, None)
        return self.active


def _annotation(
    sweep: _Sweep, pos: int, spans: list[tuple[int, int] | None]
) -> list[str]:
    candidates = sweep.at(pos, max((span[1] for span in spans if span), default=pos))
    groups, accessions = [], []
    for span in spans:
        hits = (
            [
                (accession, group)
                for lo, hi, accession, group in candidates
                if span[0] < hi and lo < span[1]
            ]
            if span
            else []
        )
        groups.append("|".join(sorted({group for _, group in hits})) or ".")
        accessions.append("|".join(sorted({accession for accession, _ in hits})) or ".")
    if all(group == "." for group in groups):
        return []
    return [
        f"{CCRE_CLASS}={','.join(groups)}",
        f"{CCRE_ID}={','.join(accessions)}",
    ]


def annotate_lines(
    lines: Iterator[str], table: Path, provenance: dict[str, str]
) -> Iterator[str]:
    available = set(
        run_command(["tabix", "-l", table], capture_output=True).stdout.splitlines()
    )
    aliases = primary_aliases()
    primary = {
        aliases[name.upper()].name: name
        for name in sorted(available)
        if name.upper() in aliases
    }
    previous_chrom, previous_pos = "", -1
    sweep = None
    with ExitStack() as track:
        for line in lines:
            if line.startswith("#"):
                if line.startswith("##wgse_ccre_") or (
                    line.startswith("##INFO=<")
                    and attributes(line.rstrip()[8:-1]).get("ID", "").strip('"')
                    in _TAGS
                ):
                    continue
                if line.startswith("#CHROM\t"):
                    yield from (header + "\n" for header in _INFO_HEADERS)
                    for key in ("release", "build", "origin", "source_sha256"):
                        yield f"##wgse_ccre_{key}={provenance[key]}\n"
                yield line
                continue
            fields = line.rstrip("\n").split("\t", 8)
            chrom, pos_text, _, ref, alts, _, _, info = fields[:8]
            pos = int(pos_text) - 1
            if chrom != previous_chrom or pos < previous_pos:
                track.close()
                name = next(
                    (a for a in chromosome_aliases(chrom) if a in available), None
                )
                if name is None and (alias := aliases.get(chrom.upper())):
                    name = primary.get(alias.name)
                sweep = (
                    _Sweep(track.enter_context(_intervals(table, name, pos)))
                    if name
                    else None
                )
            previous_chrom, previous_pos = chrom, pos
            tags = []
            if sweep is not None and _SEQUENCE.fullmatch(ref):
                spans = [
                    _changed_span(pos, ref.upper(), alt) for alt in alts.split(",")
                ]
                tags = _annotation(sweep, pos, spans)
            previous = [
                entry
                for entry in info.split(";")
                if entry not in ("", ".") and entry.partition("=")[0] not in _TAGS
            ]
            fields[7] = ";".join(previous + tags) or "."
            yield "\t".join(fields) + "\n"
