"""Per-gene pharmacogenomic evidence the report's medication review rests on."""

from __future__ import annotations

from dataclasses import dataclass

# Confidence labels a call must reach to choose between CPIC recommendations.
ADEQUATE = ("high", "moderate")


@dataclass(frozen=True)
class PgxAlternative:
    diplotype: str
    phenotype: str
    # The CPIC lookup keys PharmCAT matched the diplotype by.
    lookup_keys: tuple[str, ...]


@dataclass(frozen=True)
class PgxGene:
    """A gene's PharmCAT result and the evidence behind it: ``evidence`` is
    the confidence of the calls (or outside caller) it rests on, before any
    ambiguity lowers the report's label."""

    gene: str
    status: str
    alternatives: tuple[PgxAlternative, ...]
    evidence: str
    reasons: tuple[str, ...]
    source: str
    note: str = ""

    @property
    def adequate(self) -> bool:
        """Whether the call can narrow CPIC's recommendations: confident
        evidence and a resolved or ambiguous (not no-call) result."""
        return (
            self.evidence in ADEQUATE
            and self.status in ("resolved", "ambiguous")
            and all(a.lookup_keys for a in self.alternatives)
        )

    @property
    def phenotype(self) -> str | None:
        """The one phenotype every alternative shares, when adequate."""
        names = {a.phenotype for a in self.alternatives}
        return next(iter(names)) if self.adequate and len(names) == 1 else None

    @property
    def lookup_keys(self) -> frozenset[str]:
        return frozenset(k for a in self.alternatives for k in a.lookup_keys)

    def describe(self) -> str:
        """The call as text: 'Normal Metabolizer (*1/*1)' or its alternatives."""
        if self.status == "no_call" or not self.alternatives:
            return "no call" + (f" ({self.note})" if self.note else "")
        text = " or ".join(f"{a.phenotype} ({a.diplotype})" for a in self.alternatives)
        return f"ambiguous: {text}" if self.status == "ambiguous" else text
