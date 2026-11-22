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


def read_calls(path: Path) -> dict[int, list[Call]]:
    """mutserve's VCF calls by position."""
    calls: dict[int, list[Call]] = {}
    with _opener(path)(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            values = dict(zip(fields[8].split(":"), fields[9].split(":"), strict=False))
            for alt in fields[4].split(","):
                calls.setdefault(int(fields[1]), []).append(
                    Call(
                        ref=fields[3],
                        alt=alt,
                        genotype=values.get("GT", ""),
                        level=_number(values.get("AF")),
                        depth=_whole(values.get("DP")),
                        passed=fields[6] in ("PASS", "."),
                    )
                )
    return calls


def read_reads(path: Path) -> dict[int, Reads]:
    """mutserve's raw table (``--write-raw``) by position."""
    reads: dict[int, Reads] = {}
    with _opener(path)(path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            reads[int(row["POS"])] = Reads(
                ref=row["REF"],
                depth_fwd=_whole(row.get("COV-FWD")) or 0,
                depth_rev=_whole(row.get("COV-REV")) or 0,
                fwd={b: _number(row.get(f"%{b}")) or 0.0 for b in "ACGTDN"},
                rev={b: _number(row.get(f"%{b.lower()}")) or 0.0 for b in "ACGTDN"},
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
    """mutserve's PASS calls: homoplasmies (one allele) and heteroplasmies."""
    passed = [c for found in calls.values() for c in found if c.passed]
    mixed = sum(_mixture(c.genotype) for c in passed)
    return {"homoplasmies": len(passed) - mixed, "heteroplasmies": mixed}


def _mixture(genotype: str) -> bool:
    return len({a for a in genotype.replace("|", "/").split("/") if a}) > 1


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


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
        if overall == 0 or call.level is None:
            return Assessment(NOT_ASSESSED, ["mt_evidence_conflict"], depth=reads.depth)
        found.level = call.level
        if call.level >= HOMOPLASMIC_LEVEL:
            found.state = HOMOPLASMIC
        elif call.level >= SUPPORTED_LEVEL:
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
