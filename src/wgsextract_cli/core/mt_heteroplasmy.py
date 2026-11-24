"""mtDNA heteroplasmy evidence from mutserve, checked at reviewed variants (#1175).

``lineage mt-haplogroup --contamination`` keeps mutserve's calls (VCF, with
each call's level, FORMAT/AF) and its per-position read table (``--write-raw``:
depth and base fractions on each strand) in the mtDNA reference the reads were
called against. :func:`assess` reads them at each variant of the bundled
reviewed table (``assets/report/mt_variants.json``, built by
scripts/build_report_mt_variants.py) and says what the reads support there.

Thresholds (docs/personal-report.md explains them):

- ``HOMOPLASMIC_LEVEL`` 0.95 and ``SUPPORTED_LEVEL`` 0.10 follow gnomAD's
  mtDNA release (Laricchia et al., Genome Res 2022;32:569-582, PMID
  35074858): it reports heteroplasmy from 10% and calls 95-100% homoplasmic,
  since misaligned nuclear copies (NUMTs) made at least 43% of its 1-5% calls
  and 1% of its 5-10% calls. A call below 10% is weak evidence, not a
  negative.
- ``EVIDENCE_LEVEL`` 0.01 is the level mutserve calls from in this run
  (``--level 0.01``); ALT reads at that fraction on either strand are evidence
  worth showing even when mutserve made no call (one strand only: strand bias).
- ``MIN_DEPTH`` 100: gnomAD reads a site without a call as reference only
  above 100x. With 100 reads, a 10% heteroplasmy leaves no ALT read with
  probability 0.9^100 < 3e-5, so no ALT read at 100x or more is a supported
  negative for heteroplasmy of 10% or more (not for lower levels); fewer
  reads leave the site not assessed.

Levels are measured in the sequenced tissue (usually blood) and may differ
in other tissues and over time.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from wgsextract_cli.core.ref_library.mitochondrial import CHM13_CHRM, chm13_to_rcrs

HOMOPLASMIC_LEVEL = 0.95
SUPPORTED_LEVEL = 0.10
EVIDENCE_LEVEL = 0.01
MIN_DEPTH = 100
# How far a call's level may differ from its reads' ALT fraction: mutserve
# prints levels to 2-3 decimals; HG002's calls agree with their reads to 0.0005.
LEVEL_TOLERANCE = 0.02
# mutserve call settings (haplocheck's own workflow); the level is EVIDENCE_LEVEL.
MAPQ, BASEQ = 30, 20

RCRS = "rCRS"
# The mtDNA references whose positions can be read as the rCRS's.
ASSESSABLE_ORIGINS = (RCRS, CHM13_CHRM)

CALLS_ROLE = "heteroplasmy_calls"
SITE_EVIDENCE_ROLE = "heteroplasmy_site_evidence"

HOMOPLASMIC = "homoplasmic"
HETEROPLASMIC = "heteroplasmic"
WEAK = "weak_evidence"
NOT_DETECTED = "not_detected"
NOT_ASSESSED = "not_assessed"
STATES = (HOMOPLASMIC, HETEROPLASMIC, WEAK, NOT_DETECTED, NOT_ASSESSED)
DETECTED = (HOMOPLASMIC, HETEROPLASMIC)

REASONS = {
    "mt_low_depth": f"fewer than {MIN_DEPTH} mtDNA reads: too few to confirm a "
    f"heteroplasmy or rule out one of {SUPPORTED_LEVEL:.0%} or more",
    "mt_no_coverage": "no reads cover the site",
    "mt_below_supported_level": f"heteroplasmy below {SUPPORTED_LEVEL:.0%}, where "
    "misaligned nuclear copies of mtDNA (NUMTs) and errors dominate calls",
    "mt_not_called": "ALT reads are present, but mutserve made no call",
    "mt_reference_unconfirmed": "the run's mtDNA reference isn't confirmed as the "
    "rCRS or CHM13v2.0's chrM, so its positions can't be read as rCRS positions",
    "mt_reference_conflict": "the evidence's reference base differs from the "
    "reviewed variant's",
    "mt_evidence_conflict": "mutserve's call and its read counts disagree",
    "mt_evidence_missing": "the run's heteroplasmy evidence file is missing or "
    "unreadable",
    "mt_contamination": "haplocheck found a second mtDNA haplogroup mixed in, so "
    "heteroplasmies may come from another person's DNA",
}


# --- the reviewed variants ---------------------------------------------------


@cache
def reviewed_table() -> Mapping[str, Any]:
    """The bundled reviewed mtDNA variants and their sources."""
    text = (
        resources.files("wgsextract_cli")
        .joinpath("assets/report/mt_variants.json")
        .read_text(encoding="utf-8")
    )
    table: dict[str, Any] = json.loads(text)
    return table


# --- mutserve's evidence -----------------------------------------------------


@dataclass(frozen=True)
class Call:
    ref: str
    alt: str
    genotype: str
    level: float | None
    depth: int | None
    passed: bool
    record: int


@dataclass(frozen=True)
class Reads:
    """One position of mutserve's raw table: depth and base fractions per strand."""

    ref: str
    depth_fwd: int
    depth_rev: int
    fwd: Mapping[str, float]
    rev: Mapping[str, float]

    @property
    def depth(self) -> int:
        return self.depth_fwd + self.depth_rev

    def fraction(self, base: str) -> tuple[float, float, float]:
        """``base``'s fraction on the forward and reverse strands and overall."""
        f, r = self.fwd.get(base, 0.0), self.rev.get(base, 0.0)
        total = f * self.depth_fwd + r * self.depth_rev
        return f, r, total / self.depth if self.depth else 0.0


@dataclass
class Evidence:
    """A run's mutserve calls and reads, by position in its own coordinates."""

    calls: dict[int, list[Call]] = field(default_factory=dict)
    reads: dict[int, Reads] = field(default_factory=dict)


def _opener(path: Path) -> Any:
    return gzip.open if path.name.endswith(".gz") else open


def _levels(alts: int, genotype: str, af: str) -> dict[int, float | None]:
    """Each ALT's level (by allele index). With several ALTs, mutserve gives
    one AF per allele in the genotype's order (``2/1:0.7,0.3``: ALT 2 at 70%);
    a level it can't pair, as in a comma-decimal locale, is unknown."""
    if alts == 1:
        return {1: _fraction(af)}
    called = [int(a) for a in genotype.replace("|", "/").split("/") if a.isdigit()]
    called = [a for a in called if a > 0]
    parts = af.split(",")
    if len(parts) != len(called):
        return {}
    return {a: _fraction(p) for a, p in zip(called, parts, strict=True)}


def read_calls(path: Path) -> dict[int, list[Call]]:
    """mutserve's VCF calls by position."""
    calls: dict[int, list[Call]] = {}
    with _opener(path)(path, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream):
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            values = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
            alts = fields[4].split(",")
            genotype = values.get("GT", "")
            levels = _levels(len(alts), genotype, values.get("AF", ""))
            for index, alt in enumerate(alts, 1):
                calls.setdefault(int(fields[1]), []).append(
                    Call(
                        ref=fields[3],
                        alt=alt,
                        genotype=genotype,
                        level=levels.get(index),
                        depth=_whole(values.get("DP")),
                        passed=fields[6] in ("PASS", "."),
                        record=number,
                    )
                )
    return calls


def _depth(row: Mapping[str, str | None], column: str) -> int:
    depth = _whole(row.get(column))
    if depth is None or depth < 0:
        raise ValueError(f"position {row.get('POS')}: no {column}")
    return depth


def _fractions(
    row: Mapping[str, str | None], strand: str, depth: int
) -> dict[str, float]:
    """A strand's base fractions. On a strand with reads each must be a number
    from 0 to 1: an unreadable one is not a zero (it would make a negative)."""
    fractions = {}
    for base in "ACGTDN":
        column = f"%{base if strand == 'fwd' else base.lower()}"
        value = _fraction(row.get(column))
        if depth and value is None:
            raise ValueError(f"position {row.get('POS')}: unreadable {column}")
        fractions[base] = value if value is not None else 0.0
    return fractions


def read_reads(path: Path) -> dict[int, Reads]:
    """mutserve's raw table (``--write-raw``) by position; ValueError for a
    row that can't be read."""
    reads: dict[int, Reads] = {}
    with (
        gzip.open(path, "rt", encoding="utf-8", newline="")
        if path.suffix == ".gz"
        else path.open(encoding="utf-8", newline="")
    ) as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        required = {"POS", "REF", "COV-FWD", "COV-REV"}
        required.update(f"%{base}" for base in "ACGTDNacgtdn")
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(
                "mutserve's per-position read table lacks required columns"
            )
        for row in reader:
            fwd, rev = _depth(row, "COV-FWD"), _depth(row, "COV-REV")
            reads[int(row["POS"])] = Reads(
                ref=row["REF"],
                depth_fwd=fwd,
                depth_rev=rev,
                fwd=_fractions(row, "fwd", fwd),
                rev=_fractions(row, "rev", rev),
            )
    return reads


def depth_summary(reads: Mapping[int, Reads], length: int) -> dict[str, float]:
    """Median depth over all ``length`` positions (uncovered ones count as 0)
    and the share of positions with at least MIN_DEPTH reads."""
    depths = [reads[p].depth if p in reads else 0 for p in range(1, length + 1)]
    return {
        "median_depth": float(statistics.median(depths)) if depths else 0.0,
        "min_depth_fraction": sum(d >= MIN_DEPTH for d in depths) / length
        if length
        else 0.0,
    }


def count_calls(calls: Mapping[int, Iterable[Call]]) -> dict[str, int]:
    """mutserve's PASS records: homoplasmies (one allele) and heteroplasmies."""
    records = {
        c.record: c.genotype for found in calls.values() for c in found if c.passed
    }
    mixed = sum(_mixture(genotype) for genotype in records.values())
    return {"homoplasmies": len(records) - mixed, "heteroplasmies": mixed}


def _mixture(genotype: str) -> bool:
    return len({a for a in genotype.replace("|", "/").split("/") if a}) > 1


def _number(value: object) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fraction(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and 0 <= number <= 1 else None


def _whole(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


# --- assessing a reviewed variant -----------------------------------------------


@dataclass
class Assessment:
    state: str
    reasons: list[str]
    level: float | None = None
    depth: int | None = None
    depth_fwd: int | None = None
    depth_rev: int | None = None
    level_fwd: float | None = None
    level_rev: float | None = None


@cache
def _rcrs_to_chm13() -> dict[int, int]:
    return {p: q for q, (p, _r, _c) in chm13_to_rcrs().items()}


def evidence_position(pos: int, origin: str | None) -> int | None:
    """Where rCRS position ``pos`` is in evidence called against ``origin``."""
    if origin == RCRS:
        return pos
    if origin == CHM13_CHRM:
        return _rcrs_to_chm13().get(pos)
    return None


def assess(
    variant: Mapping[str, Any], evidence: Evidence | None, origin: str | None
) -> Assessment:
    """What the reads say at a reviewed ``variant`` (rCRS pos, ref, alt)."""
    if origin not in ASSESSABLE_ORIGINS:
        return Assessment(NOT_ASSESSED, ["mt_reference_unconfirmed"])
    if evidence is None:
        return Assessment(NOT_ASSESSED, ["mt_evidence_missing"])
    at = evidence_position(int(variant["pos"]), origin)
    if at is None:
        return Assessment(NOT_ASSESSED, ["mt_reference_conflict"])
    ref, alt = str(variant["ref"]), str(variant["alt"])
    reads = evidence.reads.get(at)
    calls = evidence.calls.get(at, [])
    if (reads and reads.ref.upper() != ref) or any(c.ref[:1] != ref for c in calls):
        return Assessment(NOT_ASSESSED, ["mt_reference_conflict"])
    if reads is None:
        if calls:
            return Assessment(NOT_ASSESSED, ["mt_evidence_conflict"])
        return Assessment(NOT_ASSESSED, ["mt_no_coverage"], depth=0)
    f, r, overall = reads.fraction(alt)
    found = Assessment(
        NOT_ASSESSED,
        [],
        depth=reads.depth,
        depth_fwd=reads.depth_fwd,
        depth_rev=reads.depth_rev,
        level_fwd=f,
        level_rev=r,
        level=overall,
    )
    low = ["mt_low_depth"] if reads.depth < MIN_DEPTH else []
    call = next((c for c in calls if c.alt == alt and c.passed), None)
    if call is not None:
        # The call must be the reads': both come from the same filtered reads.
        disagrees = (
            call.level is not None and abs(call.level - overall) > LEVEL_TOLERANCE
        ) or (
            call.depth is not None
            and abs(call.depth - reads.depth) > max(1, reads.depth // 100)
        )
        if overall == 0 or disagrees:
            return Assessment(NOT_ASSESSED, ["mt_evidence_conflict"], depth=reads.depth)
        # A level mutserve wrote in a form that can't be read: the reads' own.
        level = found.level = overall if call.level is None else call.level
        if level >= HOMOPLASMIC_LEVEL:
            found.state = HOMOPLASMIC
        elif level >= SUPPORTED_LEVEL:
            found.state = HETEROPLASMIC
        else:
            found.state = WEAK
            low.insert(0, "mt_below_supported_level")
        found.reasons = low
        return found
    if max(f, r) >= EVIDENCE_LEVEL:
        # Only a strand with reads can show the ALT is missing from it.
        one_strand = (
            min(f, r) < EVIDENCE_LEVEL and min(reads.depth_fwd, reads.depth_rev) > 0
        )
        found.state = WEAK
        found.reasons = ["strand_bias" if one_strand else "mt_not_called", *low]
        return found
    if low:
        found.reasons = ["mt_no_coverage"] if reads.depth == 0 else low
        return found
    found.state = NOT_DETECTED
    return found


def load_evidence(calls: Path | None, raw: Path | None) -> Evidence | None:
    """A run's evidence, or None when either file is missing or unreadable."""
    if calls is None or raw is None:
        return None
    try:
        return Evidence(calls=read_calls(calls), reads=read_reads(raw))
    except (OSError, ValueError, KeyError, IndexError, EOFError, csv.Error):
        return None
