"""Parameters for local comparisons across labelled VCF samples."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._params import Params, param
from wgsextract_cli.api._shared import outdir_param
from wgsextract_cli.core.table_writer import FORMATS, table_format_type
from wgsextract_cli.core.utils import UsageError


def labelled(items: tuple[str, ...], option: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        label, separator, value = item.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", label)
            or not value
        ):
            raise UsageError(f"--{option} needs LABEL=VALUE; invalid entry: {item!r}.")
        if label in values:
            raise UsageError(f"Duplicate --{option} label: {label}.")
        values[label] = value
    return values


def local_path(value: str | Path) -> Path:
    text = str(value)
    if (
        "://" in text
        or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+:/", text)
        or "##idx##" in text
        or text.startswith(("//", "\\\\"))
    ):
        raise UsageError("vcf compare accepts only ordinary local file paths.")
    return Path(value)


@dataclass(frozen=True, kw_only=True)
class VcfCompareParams(Params):
    """Compare normalized small alleles across two or more local callsets."""

    COMMAND = "vcf compare"

    callset: tuple[str, ...] = param(
        help="Local VCF, gVCF or BCF (repeat for at least two labels).",
        metavar="LABEL=PATH",
        action="append",
    )
    sample: tuple[str, ...] = param(
        (),
        help="Sample for a labelled input with multiple samples.",
        metavar="LABEL=SAMPLE",
        action="append",
    )
    gvcf: tuple[str, ...] = param(
        (),
        help="Explicit local gVCF evidence for a labelled callset.",
        metavar="LABEL=PATH",
        action="append",
    )
    mode: str = param(
        "concordance",
        choices=("concordance", "shared", "unique"),
        help="Report all pairs, or select alleles shared by an include group.",
    )
    include: tuple[str, ...] = param(
        (),
        help="Required carrier in shared/unique mode; repeat.",
        metavar="LABEL",
        action="append",
    )
    exclude: tuple[str, ...] = param(
        (),
        help="Required absence in shared/unique mode; repeat.",
        metavar="LABEL",
        action="append",
    )
    reference_evidence: str = param(
        "none",
        choices=("none", "gvcf"),
        help="Require callable gVCF evidence for strict exclusion (default: none).",
    )
    ref: Path | None = param(
        None,
        flags=("--ref",),
        shared=True,
        path="file",
        doc="Local FASTA with a .fai index (and .gzi if BGZF); enables left alignment.",
    )
    allow_unverified_build: bool = param(
        False,
        action="store_true",
        help="Allow unknown builds with a warning; never bypass a known mismatch.",
    )
    atomize: bool = param(
        False,
        action="store_true",
        help="Compare primitive sequence alleles instead of complex alleles; requires --ref.",
    )
    bed: Path | None = param(
        None,
        path="file",
        exclusive="compare_scope",
        help="BED scope in normalized coordinates (zero-based, half-open).",
    )
    region: str | None = param(
        None,
        exclusive="compare_scope",
        metavar="REGION",
        help="One contig or interval in normalized coordinates (one-based, inclusive).",
    )
    table_format: str = param(
        "tsv",
        flags=("--format",),
        choices=FORMATS,
        cli_type=table_format_type(),
        help="Concordance table format: tsv (default) or parquet (needs pyarrow).",
    )
    emit_vcf: bool = param(
        False,
        action="store_true",
        help="Write an indexed single-sample anchor VCF (first callset or first include).",
    )
    emit_partition_vcfs: bool = param(
        False,
        action="store_true",
        help="Write indexed shared, only_a and only_b VCFs for two-callset concordance.",
    )
    emit_variants: bool = param(
        False,
        action="store_true",
        help="Also write per-allele states in concordance mode (always on in family modes).",
    )
    outdir: Path | None = outdir_param()

    def __post_init__(self) -> None:
        super().__post_init__()
        labels = labelled(self.callset, "callset").keys()
        if len(labels) < 2:
            raise UsageError(
                "At least two --callset LABEL=PATH arguments are required."
            )
        if self.atomize and self.ref is None:
            raise UsageError("--atomize requires an indexed local --ref.")
        if self.emit_partition_vcfs and (
            self.mode != "concordance" or len(labels) != 2
        ):
            raise UsageError(
                "--emit-partition-vcfs requires exactly two callsets in concordance mode."
            )
        for option, given in (
            ("sample", labelled(self.sample, "sample")),
            ("gvcf", labelled(self.gvcf, "gvcf")),
            ("include", self.include),
            ("exclude", self.exclude),
        ):
            if unknown := set(given) - labels:
                raise UsageError(
                    f"Unknown --{option} labels: {', '.join(sorted(unknown))}."
                )
        if self.gvcf and self.reference_evidence != "gvcf":
            raise UsageError("--gvcf requires --reference-evidence gvcf.")
        if self.mode == "concordance" and (self.include or self.exclude):
            raise UsageError("--include and --exclude require shared or unique mode.")
        if self.mode == "shared" and len(set(self.include)) < 2:
            raise UsageError("Shared mode requires at least two included labels.")
        if self.mode == "unique" and not (self.include and self.exclude):
            raise UsageError("Unique mode requires an included and an excluded label.")
        if set(self.include) & set(self.exclude):
            raise UsageError("A label cannot be both included and excluded.")
        if self.bed is not None and self.region is not None:
            raise UsageError("Choose either --bed or --region.")
        for _, path in self.implied_paths():
            local_path(path)

    def implied_paths(self) -> Iterator[tuple[str, Path]]:
        for option, entries in (("callset", self.callset), ("gvcf", self.gvcf)):
            for label, value in labelled(entries, option).items():
                yield f"{option}[{label}]", local_path(value)
        if self.ref is not None:
            yield "ref_index", Path(f"{self.ref}.fai")
            yield "ref_index", Path(f"{self.ref}.gzi")
