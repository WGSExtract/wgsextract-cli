"""The `qc roh` result document, ``wgsextract.roh/1`` (#1174).

Written as ``<stem>.roh.json`` and returned as the run's ``details``; its JSON
Schema (assets/schemas/wgsextract.roh.1.schema.json) is generated from these
dataclasses by scripts/gen_output_schemas.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final, Literal

from wgsextract_cli.core.output_schema import Constraints

SCHEMA_ID: Final = "wgsextract.roh/1"
FLAG_CODE: Final = "investigate_possible_isodisomy"

Position = Literal["terminal", "interstitial", "undetermined"]


@dataclass(frozen=True)
class PopulationFrequencies:
    path: str
    kind: Literal["vcf", "table"]
    tag: Annotated[str | None, Constraints(description="INFO field of a VCF source.")]
    release: Annotated[
        str | None, Constraints(description="As declared with --af-release.")
    ]
    build: str
    size: int
    digest: Annotated[
        str | None,
        Constraints(
            description="sha256:<hex> of the whole file up to 64 MiB, else a "
            "wgse-fp1 sampled fingerprint."
        ),
    ]
    digest_strength: Literal["full", "sampled", "metadata"]
    prepared: Annotated[
        Literal["built", "reused", "uncached"],
        Constraints(
            description="Whether the bcftools AF table was built now, reused "
            "from the reference library's cache, or built for this run only."
        ),
    ]
    prepared_reason: str | None
    prepared_sites: Annotated[
        int, Constraints(description="Biallelic SNVs with 0 < AF < 1.", minimum=0)
    ]


@dataclass(frozen=True)
class Condition:
    name: str
    upd: Annotated[
        str,
        Constraints(description="The UPD form that causes it, e.g. maternal UPD(15)."),
    ]


@dataclass(frozen=True)
class ImprintedRegion:
    id: str
    band: str | None
    chrom: str
    start: Annotated[int, Constraints(description="1-based, inclusive.")]
    end: int
    genes: list[str]
    conditions: list[Condition]


@dataclass(frozen=True)
class ImprintedRegions:
    build: str
    source: str
    path: str | None
    citations: list[str]
    regions: list[ImprintedRegion]


@dataclass(frozen=True)
class Scope:
    regions: Annotated[
        list[str], Constraints(description="The assessed autosomes or ranges.")
    ]
    contigs: list[str]
    contigs_without_markers: list[str]
    declared_bp: Annotated[
        int, Constraints(description="Length of the declared regions.", minimum=0)
    ]
    denominator_bp: Annotated[
        int,
        Constraints(
            description="F_ROH denominator: on each assessed contig, the span "
            "from its first to its last marker, less gaps between markers over "
            "max_marker_gap_bp.",
            minimum=0,
        ),
    ]
    whole_genome: Annotated[
        bool,
        Constraints(
            description="True only when all 22 autosomes are in scope whole "
            "and have markers."
        ),
    ]


@dataclass(frozen=True)
class Markers:
    records: Annotated[
        int,
        Constraints(
            description="The sample's biallelic SNV records in scope with PASS or no FILTER.",
            minimum=0,
        ),
    ]
    missing_genotype: int
    weak_genotype: Annotated[int, Constraints(description="GQ below min_gq.")]
    hom_ref: int
    het: int
    hom_alt: int
    not_modelled: Annotated[
        int,
        Constraints(
            description="Calls bcftools roh skipped: not in the AF table, or a "
            "second record at a position."
        ),
    ]
    used: Annotated[int, Constraints(description="Sites bcftools roh modelled.")]
    min_gq: int | None
    reference_calls: Annotated[
        bool,
        Constraints(
            description="Whether the VCF has hom-ref calls; a variants-only VCF "
            "gives bcftools roh only heterozygous and hom-alt sites."
        ),
    ]


@dataclass(frozen=True)
class Parameters:
    bcftools: str | None
    gt_error_phred: Annotated[
        int,
        Constraints(
            description="bcftools roh --GTs-only: genotypes are used, not PLs."
        ),
    ]
    hw_to_az: float
    az_to_hw: float
    flag_terminal_bp: int
    flag_interstitial_bp: int
    flag_min_markers: int
    flag_min_quality: float
    froh_min_bp: int
    max_marker_gap_bp: Annotated[
        int,
        Constraints(
            description="Runs are split, and the denominator leaves out, gaps "
            "between consecutive markers longer than this."
        ),
    ]


@dataclass(frozen=True)
class Segment:
    chrom: str
    start: Annotated[int, Constraints(description="First marker in the run, 1-based.")]
    end: Annotated[int, Constraints(description="Last marker in the run.")]
    length_bp: int
    markers: int
    quality: Annotated[
        float,
        Constraints(description="bcftools roh's average forward-backward phred score."),
    ]
    position: Annotated[
        Position,
        Constraints(
            description="terminal: reaches the last (or, except on acrocentric "
            "chromosomes, the first) marker of a whole assessed chromosome, "
            "within 1 Mb of its end; undetermined: that marker is further "
            "from the end, or the scope was part of the chromosome."
        ),
    ]
    imprinted_regions: list[str]
    flagged: bool


@dataclass(frozen=True)
class Flag:
    code: Literal["investigate_possible_isodisomy"]
    chrom: str
    start: int
    end: int
    length_bp: int
    position: Position
    criterion: str
    regions: list[ImprintedRegion]
    message: str


@dataclass(frozen=True)
class Summary:
    segments: int
    total_bp: int
    longest_bp: int
    segments_over_1mb: int
    segments_over_5mb: int
    froh: Annotated[
        float | None,
        Constraints(
            description="Runs of at least froh_min_bp over denominator_bp, within "
            "the assessed scope only; null without markers."
        ),
    ]
    chromosomes_with_long_roh: Annotated[
        list[str], Constraints(description="Chromosomes with a run of 5 Mb or more.")
    ]


@dataclass(frozen=True)
class RohDocument:
    schema: Literal["wgsextract.roh/1"]
    input: str
    sample: str
    build: str
    scope: Scope
    population_frequencies: PopulationFrequencies
    imprinted_regions: ImprintedRegions
    markers: Markers
    parameters: Parameters
    summary: Summary
    segments: list[Segment]
    flags: list[Flag]
    notes: list[str]
