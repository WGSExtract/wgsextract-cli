"""Confidence finding rows, summary manifests, and TSV output."""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._confidence_rules import LABELS, CallConfidence, Thresholds, _confidence_policy
from .atomic_output import atomic_output
from .paths import PathInput
from .results import Scalar

SCHEME = "wgsextract.confidence/1"
GUIDE = "docs/variant-confidence.md"


# Findings listed in the result manifest; the TSV always has all of them.
MAX_MANIFEST_FINDINGS = 1000
DISCLAIMER = (
    "Confidence labels describe the technical support for each call in this "
    "sequencing run, not whether a variant causes disease. Treat any finding as "
    "a research result: confirm it with an accredited clinical laboratory "
    "(e.g. a CLIA/CAP-certified test on a new sample) before acting on it."
)


@dataclass
class Finding:
    chrom: str
    pos: int
    ref: str
    alt: str
    genotype: str
    confidence: CallConfidence
    annotations: dict[str, str] = field(default_factory=dict)
    # FORMAT/PS: calls phased (``|``) in one phase set are on known copies.
    phase_set: str = ""

    def row(self, columns: Sequence[str]) -> dict[str, str]:
        c = self.confidence
        return {
            "chrom": self.chrom,
            "pos": str(self.pos),
            "ref": self.ref,
            "alt": self.alt,
            "genotype": self.genotype,
            "zygosity": c.zygosity,
            "confidence": c.label,
            "reasons": "|".join(c.reasons),
            "depth": _format(c.depth),
            "genotype_quality": _format(c.genotype_quality),
            "alt_fraction": _format(c.alt_fraction, 3),
            "phase_set": self.phase_set,
            **{column: self.annotations.get(column, "") for column in columns},
        }

    def manifest(self) -> dict[str, Any]:
        c = self.confidence
        return {
            "chrom": self.chrom,
            "pos": self.pos,
            "ref": self.ref,
            "alt": self.alt,
            "genotype": self.genotype,
            "zygosity": c.zygosity,
            "confidence": c.label,
            "reasons": list(c.reasons),
            "depth": c.depth,
            "genotype_quality": c.genotype_quality,
            "alt_fraction": None
            if c.alt_fraction is None
            else round(c.alt_fraction, 4),
            **({"phase_set": self.phase_set} if self.phase_set else {}),
            **{k: v for k, v in self.annotations.items() if v},
        }


def _format(value: float | None, digits: int = 0) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}" if digits else f"{value:g}"


@dataclass
class ConfidenceSummary:
    caller: str
    thresholds: Thresholds
    sample: str | None = None
    contamination: float | None = None
    records: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    reasons: Counter[str] = field(default_factory=Counter)
    not_carried: int = 0
    rare_imputed: int = 0
    findings: list[Finding] = field(default_factory=list)
    # The gVCF's states at the sites checked (add_reference_evidence).
    reference_evidence: dict[str, Any] | None = None

    @property
    def carried(self) -> int:
        return sum(self.counts[label] for label in LABELS)

    def metrics(self, prefix: str = "confidence") -> dict[str, Scalar]:
        return {
            **{f"{prefix}_{label}": self.counts[label] for label in LABELS},
            f"{prefix}_not_carried": self.not_carried,
        }

    def details(self, output: str | None, role: str) -> dict[str, Any]:
        listed = self.findings[:MAX_MANIFEST_FINDINGS]
        return {
            "scheme": SCHEME,
            "role": role,
            "output": None if output is None else str(Path(output).absolute()),
            "sample": self.sample,
            "caller": self.caller,
            "policy": _confidence_policy(self.caller),
            "thresholds": asdict(self.thresholds),
            "contamination": self.contamination,
            "counts": {label: self.counts[label] for label in LABELS},
            "not_carried": self.not_carried,
            "rare_imputed_not_listed": self.rare_imputed,
            "reasons": dict(sorted(self.reasons.items())),
            "findings": [finding.manifest() for finding in listed],
            "findings_truncated": len(self.findings) > len(listed),
            **(
                {"reference_evidence": self.reference_evidence}
                if self.reference_evidence
                else {}
            ),
            "disclaimer": DISCLAIMER,
            "guide": GUIDE,
        }


TABLE_COLUMNS = (
    "chrom", "pos", "ref", "alt", "genotype", "zygosity", "confidence",
    "reasons", "depth", "genotype_quality", "alt_fraction", "phase_set",
)  # fmt: skip


def write_table(
    path: PathInput,
    summary: ConfidenceSummary,
    columns: Sequence[str] = (),
    title: str = "Variant calls",
) -> None:
    """A TSV of every labelled finding, headed by '#' comment lines that state
    the scheme, thresholds and the disclaimer."""
    t = summary.thresholds
    with atomic_output(path, retire_indexes=False) as staged:
        with open(staged, "w", newline="", encoding="utf-8") as handle:
            handle.write(
                f"# {title}: wgsextract confidence labels ({SCHEME}; {GUIDE})\n"
                f"# Caller: {summary.caller}; sample: {summary.sample or '.'}; "
                f"depth low<{t.min_depth:g} high>={t.high_depth:g}; GQ "
                f"low<{t.min_gq:g} high>={t.high_gq:g}\n"
                f"# {DISCLAIMER}\n"
            )
            writer = csv.DictWriter(
                handle,
                fieldnames=[*TABLE_COLUMNS, *columns],
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            for finding in summary.findings:
                writer.writerow(finding.row(columns))
