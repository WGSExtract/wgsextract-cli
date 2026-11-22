"""Classify the technical confidence of one carried allele from VCF evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .config import typed_settings
from .imputed_vcf import IMPUTED_TAG
from .utils import UsageError

HIGH, MODERATE, LOW = "high", "moderate", "low"
LABELS = (HIGH, MODERATE, LOW)

# Fixed rule constants (docs/variant-confidence.md explains each).
HET_LOW_FRACTION = (0.2, 0.8)  # het alt fraction outside: low
HET_HIGH_FRACTION = (
    0.3,
    0.7,
)  # het alt fraction outside (but inside the above): moderate
HOM_LOW_FRACTION = 0.8  # hom/hemizygous alt fraction below: low
HOM_HIGH_FRACTION = 0.9  # ... below (but at least the above): moderate
# INFO/MQ below: low. RMS MQ (the VCF spec's MQ; GATK, DRAGEN) uses GATK's
# hard filter. A mean MQ (bcftools "Average mapping quality") sits well below
# the RMS of the same reads, so it uses MAPQ 20, a 1% chance of misplacement.
MIN_MAPPING_QUALITY = {"rms": 40.0, "mean": 20.0}
# INFO/QD below: low (GATK hard filter). Only GATK's QUAL is on the scale it
# assumes: DRAGEN's recalibrated QUAL puts half its good calls under QD 2.
MIN_QD = 2.0
MAX_FS = {"snv": 60.0, "indel": 200.0}  # INFO/FS above: low (GATK hard filter)
MAX_SOR = {"snv": 3.0, "indel": 10.0}  # SOR above: low (GATK hard filter)
SYMBOLIC_REFERENCE_ALTS = {"<*>", "<NON_REF>", "<X>"}
# INFO tag marking one ALT split from a multiallelic record (bcftools norm -m
# --old-rec-tag, vt decompose). Its PL is trimmed to REF/this ALT, dropping a
# called 1/2, and site-level DP4/SB still count every ALT's reads.
SPLIT_TAG = "OLD_MULTIALLELIC"


@dataclass(frozen=True)
class Thresholds:
    min_depth: float
    high_depth: float
    min_gq: float
    high_gq: float

    def __post_init__(self) -> None:
        if self.high_depth < self.min_depth or self.high_gq < self.min_gq:
            raise UsageError(
                "Confidence thresholds need LOW <= HIGH (depth "
                f"{self.min_depth:g}/{self.high_depth:g}, genotype quality "
                f"{self.min_gq:g}/{self.high_gq:g})."
            )


def thresholds_from(args: object | None = None) -> Thresholds:
    """Thresholds from config.toml, overridden by --confidence-depth/-gq."""
    depth = getattr(args, "confidence_depth", None)
    gq = getattr(args, "confidence_gq", None)
    configured = typed_settings()
    return Thresholds(
        min_depth=depth[0] if depth else configured.confidence_min_depth,
        high_depth=depth[1] if depth else configured.confidence_high_depth,
        min_gq=gq[0] if gq else configured.confidence_min_gq,
        high_gq=gq[1] if gq else configured.confidence_high_gq,
    )


def _confidence_policy(caller: str) -> str:
    return "unsupported_somatic" if caller == "mutect2" else "germline"


def _number(value: str | None) -> float | None:
    if value is None or value in {"", "."}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _numbers(value: str | None) -> list[float | None]:
    if value is None or value in {"", "."}:
        return []
    return [_number(part) for part in value.split(",")]


def strand_odds_ratio(ref_fwd: float, ref_rev: float, alt_fwd: float, alt_rev: float) -> float:  # fmt: skip
    """GATK's StrandOddsRatio from forward/reverse ref and alt counts."""
    rf, rr, af, ar = ref_fwd + 1, ref_rev + 1, alt_fwd + 1, alt_rev + 1
    ratio = (rf * ar) / (rr * af)
    return (
        math.log(ratio + 1 / ratio)
        + math.log(min(rf, rr) / max(rf, rr))
        - math.log(min(af, ar) / max(af, ar))
    )


def _genotype_quality(fmt: Mapping[str, str], split: bool) -> float | None:
    gq = _number(fmt.get("GQ"))
    if gq is not None or split:
        return gq
    likelihoods = sorted(v for v in _numbers(fmt.get("PL")) if v is not None)
    if len(likelihoods) >= 2:
        return min(likelihoods[1] - likelihoods[0], 99.0)
    return None


def _depth(
    fmt: Mapping[str, str], info: Mapping[str, str], split: bool
) -> float | None:
    depth = _number(fmt.get("DP"))
    if depth is not None:
        return depth
    # A split record's AD keeps only REF and its own ALT.
    counts = [] if split else [v for v in _numbers(fmt.get("AD")) if v is not None]
    if counts:
        return math.fsum(counts)
    # INFO/DP is the site's depth over every sample: only one sample is judged.
    return _number(info.get("DP"))


def alt_fraction(
    alt: int, n_alts: int, fmt: Mapping[str, str], info: Mapping[str, str], split: bool
) -> float | None:
    """The fraction of the sample's reads carrying ALT number ``alt`` (1-based):
    FORMAT/AD over the larger of its sum and FORMAT/DP, else the caller's
    per-ALT FORMAT/VAF (DeepVariant) or FORMAT/AF (Clair3, Mutect2), else a
    biallelic record's INFO/DP4 (pass ``info`` only for a single-sample VCF).
    None when nothing measures it, or AD counts no informative read."""
    counts = _numbers(fmt.get("AD"))
    alt_count = counts[alt] if len(counts) > alt else None
    format_depth = _number(fmt.get("DP"))
    # A split record's AD keeps only REF and this ALT: only FORMAT/DP still
    # counts the reads of the ALTs split off.
    if alt_count is not None and (format_depth is not None or not split):
        known = [v for v in counts if v is not None]
        if not math.fsum(known):
            return None  # no informative read: AD measures nothing
        # Without FORMAT/DP, a missing AD count would shrink the denominator.
        if format_depth is not None or len(known) == len(counts):
            return alt_count / max(math.fsum(known), format_depth or 0)
    for key in ("VAF", "AF"):  # DeepVariant VAF, Clair3 AF: per ALT
        fractions = _numbers(fmt.get(key))
        if len(fractions) >= alt and fractions[alt - 1] is not None:
            return fractions[alt - 1]
    dp4 = [v or 0.0 for v in _numbers(info.get("DP4"))]
    if n_alts == 1 and not split and len(dp4) == 4 and math.fsum(dp4):
        return (dp4[2] + dp4[3]) / math.fsum(dp4)
    return None


def _strand_counts(
    fmt: Mapping[str, str], info: Mapping[str, str], n_alts: int, split: bool
) -> list[float] | None:
    for counts in (_numbers(fmt.get("SB")), _numbers(info.get("DP4"))):
        if (
            n_alts == 1
            and not split
            and len(counts) == 4
            and all(v is not None for v in counts)
        ):
            return [v or 0.0 for v in counts]
    return None


@dataclass
class CallConfidence:
    label: str
    reasons: list[str]
    zygosity: str
    depth: float | None
    genotype_quality: float | None
    alt_fraction: float | None


def classify(
    *,
    alt: int,
    alleles: Sequence[str],
    ref: str,
    alt_allele: str,
    filters: str,
    info: Mapping[str, str],
    fmt: Mapping[str, str],
    n_alts: int,
    thresholds: Thresholds,
    mq_kind: str = "rms",
    caller: str = "unknown",
    samples: int = 1,
    contaminated: bool = False,
    reference: bool = False,
    ploidy: int | None = None,
) -> CallConfidence | None:
    """The confidence of ALT number ``alt`` (1-based), or None when the sample
    doesn't carry it."""
    copies = sum(allele == ("0" if reference else str(alt)) for allele in alleles)
    no_call = reference and (not alleles or any(a != "0" for a in alleles))
    if not copies and not no_call:
        return None
    zygosity = (
        "haploid"
        if (ploidy if ploidy is not None else len(alleles)) == 1
        else ("hom" if copies == len(alleles) else "het")
    )
    kind = "snv" if len(ref) == len(alt_allele) == 1 else "indel"
    low: list[str] = []
    moderate: list[str] = []
    if no_call:
        low.append("no_call")

    if filters not in {"PASS", "."}:
        low.append("filtered")
    if caller == "mutect2":
        # Preserve descriptive counts, but never interpret somatic GT/GQ/VAF
        # against germline genotype expectations or hard-filter thresholds.
        split = SPLIT_TAG in info
        counts_info = info if samples == 1 else {}
        return CallConfidence(
            LOW,
            [*low, "unsupported_somatic"],
            "unassessed",
            _depth(fmt, counts_info, split),
            _number(fmt.get("GQ")),
            alt_fraction(alt, n_alts, fmt, counts_info, split),
        )
    if IMPUTED_TAG in info:
        # No reads behind it to judge: its own probability is in FORMAT/GP.
        return CallConfidence(LOW, [*low, "imputed"], zygosity, None, None, None)

    split = SPLIT_TAG in info
    # INFO/DP and DP4 pool every sample's reads: they are this call's evidence
    # only in a single-sample VCF.
    counts_info = info if samples == 1 else {}
    scale = 0.5 if zygosity == "haploid" else 1.0
    depth = _depth(fmt, counts_info, split)
    minimum_depth = _number(fmt.get("MIN_DP")) if reference else None
    if minimum_depth is not None:
        depth = minimum_depth if depth is None else min(depth, minimum_depth)
    if depth is None:
        moderate.append("depth_unknown")
    elif depth < thresholds.min_depth * scale:
        low.append("low_depth")
    elif depth < thresholds.high_depth * scale:
        moderate.append("moderate_depth")

    gq = _genotype_quality(fmt, split)
    if gq is None:
        moderate.append("gq_unknown")
    elif gq < thresholds.min_gq:
        low.append("low_gq")
    elif gq < thresholds.high_gq:
        moderate.append("moderate_gq")
    if reference and (depth is None or gq is None):
        low.append("reference_metrics_unknown")

    fraction = alt_fraction(alt, n_alts, fmt, counts_info, split)
    if reference:
        fraction = None
    elif fraction is None:
        moderate.append("allele_balance_unknown")
    elif zygosity == "het":
        if not HET_LOW_FRACTION[0] <= fraction <= HET_LOW_FRACTION[1]:
            low.append("allele_imbalance")
        elif not HET_HIGH_FRACTION[0] <= fraction <= HET_HIGH_FRACTION[1]:
            moderate.append("skewed_allele_balance")
    elif fraction < HOM_LOW_FRACTION:
        low.append("allele_imbalance")
    elif fraction < HOM_HIGH_FRACTION:
        moderate.append("skewed_allele_balance")

    if depth is None and gq is None and fraction is None:
        low.append("no_call_metrics")

    mq = _number(info.get("MQ"))
    if mq is not None and mq < MIN_MAPPING_QUALITY[mq_kind]:
        low.append("low_mapq")

    fs, sor = _number(info.get("FS")), _number(info.get("SOR"))
    if sor is None and fs is None:
        counts = _strand_counts(fmt, counts_info, n_alts, split)
        sor = strand_odds_ratio(*counts) if counts else None
    if (fs is not None and fs > MAX_FS[kind]) or (
        sor is not None and sor > MAX_SOR[kind]
    ):
        low.append("strand_bias")

    qd = _number(info.get("QD")) if caller == "gatk" and not reference else None
    if qd is not None and qd < MIN_QD:
        low.append("low_qd")

    if contaminated:
        moderate.append("sample_contaminated")

    label = LOW if low else MODERATE if moderate else HIGH
    return CallConfidence(label, low + moderate, zygosity, depth, gq, fraction)
