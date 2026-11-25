"""Sample fact definitions, detector versions, and recorded fact values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from wgsextract_cli.core.sample_types import Platform, Sex
from wgsextract_cli.core.sample_view import FactView
from wgsextract_cli.core.sample_workspace_format import WORKSPACE_FILE

SEXES: tuple[str, ...] = get_args(Sex)
PLATFORMS: tuple[str, ...] = get_args(Platform)
_ALIASES = {
    "m": "Male",
    "f": "Female",
    "nanopore": "ont",
    "oxford nanopore": "ont",
    "hifi": "pacbio",
    "mgi": "dnbseq",
    "bgi": "dnbseq",
    "bgiseq": "dnbseq",
}

Source = Literal["override", "detected"]


@dataclass(frozen=True)
class FactSpec:
    """One kind of fact. ``choices`` makes it user-overridable. ``version`` is
    its detector's: bump it when detection changes so recorded facts are
    detected again."""

    name: str
    description: str
    choices: tuple[str, ...] | None = None
    version: int = 1

    @property
    def overridable(self) -> bool:
        return self.choices is not None

    def normalize(self, value: object) -> str:
        """The canonical spelling of a user's value; ValueError if not allowed."""
        assert self.choices is not None
        text = str(value).strip()
        lowered = _ALIASES.get(text.lower(), text).lower()
        for choice in self.choices:
            if choice.lower() == lowered:
                return choice
        raise ValueError(
            f"{self.name} must be one of {', '.join(self.choices)}, not {value!r}"
        )


FACTS: dict[str, FactSpec] = {
    spec.name: spec
    for spec in (
        FactSpec("sex", "Declared sex or observed non-PAR depth evidence.", SEXES, 6),
        FactSpec(
            "platform",
            "Sequencing platform, from read groups, read names or a bounded FASTQ sample.",
            PLATFORMS,
            4,
        ),
        # 2: a VCF's ##reference path counts only by its file name.
        # 3: .vcf.bgz and upper-case VCF names are read as VCFs (#1243).
        # 4: metadata and contig counts are hints, not confirmed builds (#1346).
        FactSpec("build", "Reference build the file is aligned to.", version=4),
        FactSpec(
            "somalier_sex",
            "Male or Female, from chrX heterozygosity and chrY depth (qc relate).",
        ),
        FactSpec(
            "superpopulation",
            "1000 Genomes superpopulation (AFR, AMR, EAS, EUR, SAS; qc relate).",
        ),
        FactSpec("read_length", "Mean read length in bases."),
        # Version 2: long reads' depth is sampled, not counted (#427).
        FactSpec("mean_coverage", "Mean mapped read depth over the genome.", None, 2),
        # Version 3: multi-reference CRAM containers are sampled on the contigs
        # their index lists (#339).
        FactSpec(
            "target_depths",
            "Mean mapped depth of the autosomes, chrX, chrY and mtDNA.",
            None,
            3,
        ),
        FactSpec(
            "contamination",
            "Fraction of the reads from another person (VerifyBamID2 FREEMIX).",
        ),
        FactSpec(
            "ancestry",
            "Shares of the 7 continental HGDP+1KG reference components "
            "(qc ancestry; reference proportions, not ethnicity).",
        ),
    )
}
OVERRIDABLE = tuple(name for name, spec in FACTS.items() if spec.overridable)
# Facts of the person rather than of one file: overridden folder-wide in a
# genome folder, which holds one person's files.
PERSON_FACTS = ("sex",)


@dataclass(frozen=True)
class Fact:
    """A fact in effect: the user's override or a current detected value."""

    name: str
    value: object
    source: Source
    file: str | None = None
    fingerprint: str | None = None
    method: str | None = None
    confidence: str | None = None
    version: int | None = None
    by: str | None = None
    at: str | None = None
    statistics: dict[str, float] | None = None
    evidence_fingerprint: str | None = None

    def describe(self) -> str:
        if self.source == "override":
            scope = f" for {self.file}" if self.file else ""
            return f"your override{scope} in {WORKSPACE_FILE}"
        detail = ", ".join(x for x in (self.method, self.confidence) if x)
        when = (self.at or "")[:10]
        return (
            f"recorded in {WORKSPACE_FILE} from {self.file}"
            + (f" by {self.by}" if self.by else "")
            + (f" on {when}" if when else "")
            + (f": {detail}" if detail else "")
        )

    def to_dict(self) -> FactView:
        result: FactView = {
            "name": self.name,
            "value": self.value,
            "source": self.source,
        }
        if self.file is not None:
            result["file"] = self.file
        if self.fingerprint is not None:
            result["fingerprint"] = self.fingerprint
        if self.method is not None:
            result["method"] = self.method
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.version is not None:
            result["version"] = self.version
        if self.by is not None:
            result["by"] = self.by
        if self.at is not None:
            result["at"] = self.at
        if self.statistics is not None:
            result["statistics"] = self.statistics
        if self.evidence_fingerprint is not None:
            result["evidence_fingerprint"] = self.evidence_fingerprint
        return result


def detected_fact(
    name: str, key: str, fingerprint: object, fact: Mapping[str, object]
) -> Fact:
    version = fact.get("version")
    return Fact(
        name=name,
        value=fact["value"],
        source="detected",
        file=key,
        fingerprint=_text(fingerprint),
        method=_text(fact.get("method")),
        confidence=_text(fact.get("confidence")),
        version=version if isinstance(version, int) else None,
        by=_text(fact.get("by")),
        at=_text(fact.get("at")),
        statistics=_statistics(fact.get("statistics")),
        evidence_fingerprint=_text(fact.get("evidence_fingerprint")),
    )


def _statistics(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: float(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, (int, float))
    }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
