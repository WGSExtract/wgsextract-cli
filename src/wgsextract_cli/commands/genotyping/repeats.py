"""`typing str`: repeat expansions with ExpansionHunter, LongTR or STRkit.

ExpansionHunter (Illumina; Apache-2.0) sizes the 31 disease-causing short
tandem repeats of its shipped catalog (HTT, FMR1, C9ORF72, ...) from short
reads, including expansions longer than a read. Nanopore and PacBio reads go
to LongTR (or STRkit), which size the same repeats from reads that span them
(long_read_repeats.py). Each allele is then put in STRchive's normal,
intermediate or pathogenic range for its disease.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.genotyping import TypingStrParams
from wgsextract_cli.commands.info.metrics import sample_sex
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.pixi_tools import pixi_env_prefix
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.sequencing import input_platform
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command
from wgsextract_cli.core.variant_files import get_bam_header

from . import long_read_repeats
from .common import Alignment, RepeatCall, env_tool, open_alignment

LONG_READ_PLATFORMS = ("ont", "pacbio")
LONG_READ_CALLERS = ("longtr", "strkit")
TOOL_NAMES = {
    "expansionhunter": "ExpansionHunter",
    "longtr": "LongTR",
    "strkit": "STRkit",
}


@dataclass(frozen=True)
class Threshold:
    disease: str
    inheritance: str  # AD, AR, XD, XR, or AD/AR
    benign_max: int | None  # None: no intermediate range
    pathogenic_min: int
    premutation_min: int | None = None


# Repeat counts from STRchive (https://strchive.org, MIT license), keyed by the
# catalog's VariantId for each pathogenic repeat. PABPN1 is in the catalog's
# units, the (GCG) tract before (GCA)3GCG: STRchive's GCN counts minus 4.
# DIP2B and NIPA1 are left out: their disease association is disputed.
THRESHOLDS = {
    "AFF2": Threshold("FRAXE", "XR", 39, 201),
    "AR": Threshold("SBMA", "XR", 34, 38),
    "ATN1": Threshold("DRPLA", "AD", 35, 48),
    "ATXN1": Threshold("SCA1", "AD", 35, 39),
    "ATXN10": Threshold("SCA10", "AD", 32, 800),
    "ATXN2": Threshold("SCA2", "AD/AR", 28, 35),
    "ATXN3": Threshold("SCA3", "AD", 44, 60),
    "ATXN7": Threshold("SCA7", "AD", 27, 37),
    "ATXN8OS": Threshold("SCA8", "AD", 50, 71),
    "C9ORF72": Threshold("FTD/ALS", "AD", 23, 31),
    "CACNA1A": Threshold("SCA6", "AD", 18, 21),
    "CBL": Threshold("Jacobsen syndrome", "AD", 79, 101),
    "CNBP": Threshold("DM2", "AD", 26, 75),
    "CSTB": Threshold("EPM1", "AR", 3, 30),
    "DMPK": Threshold("DM1", "AD", 34, 50),
    "FMR1": Threshold("Fragile X", "XD", 44, 201, premutation_min=55),
    "FXN": Threshold("Friedreich ataxia", "AR", 33, 56),
    "GIPC1": Threshold("OPDM2", "AD", 32, 73),
    "GLS": Threshold("GDPAG", "AR", 38, 680),
    "HTT": Threshold("Huntington disease", "AD", 26, 36),
    "JPH3": Threshold("HDL2", "AD", 28, 40),
    "NOP56": Threshold("SCA36", "AD", 14, 650),
    "NOTCH2NL": Threshold("NIID", "AD", 37, 66),
    "PABPN1": Threshold("OPMD", "AD/AR", 6, 8),
    "PHOX2B": Threshold("CCHS", "AD", 20, 26),
    "PPP2R2B": Threshold("SCA12", "AD", 32, 51),
    # Pathogenic RFC1 expansions are AAGGG; the benign AAAAG and AAAGG ones
    # reach the same lengths, and ExpansionHunter's count doesn't tell them
    # apart, so only the length is reported, without an intermediate range.
    "RFC1": Threshold("CANVAS", "AR", None, 400),
    "TBP": Threshold("SCA17", "AD", 40, 49),
    "TCF4": Threshold("FECD3", "AD", 39, 51),
}
MOTIF_UNCHECKED = {"RFC1"}


def parse_expansionhunter(json_path: str) -> list[RepeatCall]:
    with open(json_path) as f:
        loci = json.load(f).get("LocusResults", {})
    calls = []
    for locus in loci.values():
        for variant_id, variant in (locus.get("Variants") or {}).items():
            genotype = variant.get("Genotype")
            if not genotype:
                continue
            intervals = [
                tuple(int(bound) for bound in ci.split("-"))
                for ci in variant["GenotypeConfidenceInterval"].split("/")
            ]
            calls.append(
                RepeatCall(
                    variant_id=variant_id,
                    repeat_unit=variant.get("RepeatUnit", ""),
                    region=variant.get("ReferenceRegion", ""),
                    alleles=tuple(int(a) for a in genotype.split("/")),
                    intervals=tuple((low, high) for low, high in intervals),
                    coverage=float(locus.get("Coverage") or 0),
                )
            )
    return calls


def classify(call: RepeatCall, threshold: Threshold | None) -> str:
    """normal, intermediate, premutation, carrier or pathogenic (or "-" for a
    repeat without thresholds), with a note when an allele's confidence
    interval reaches the pathogenic range but its call doesn't."""
    clipped = (
        f" ({call.clipped_reads} reads clipped at the repeat: an expansion may "
        "be longer than called)"
        if call.clipped_reads
        else ""
    )
    if not call.alleles:
        return "no call" + clipped
    if threshold is None:
        return "-" + clipped
    expanded = sum(a >= threshold.pathogenic_min for a in call.alleles)
    recessive = threshold.inheritance == "AR" or (
        threshold.inheritance == "XR" and len(call.alleles) > 1
    )
    if expanded and recessive and expanded < len(call.alleles):
        status = "carrier"
    elif expanded:
        status = "pathogenic"
    elif threshold.premutation_min is not None and any(
        a >= threshold.premutation_min for a in call.alleles
    ):
        status = "premutation"
    elif threshold.benign_max is not None and any(
        a > threshold.benign_max for a in call.alleles
    ):
        status = "intermediate"
    else:
        status = "normal"
    if status != "pathogenic" and any(
        allele < threshold.pathogenic_min <= high
        for allele, (_low, high) in zip(
            call.alleles, call.intervals or (), strict=False
        )
    ):
        status += " (CI reaches pathogenic)"
    if expanded and call.variant_id in MOTIF_UNCHECKED:
        status = f"{status}-length expansion, motif unchecked"
    return status + clipped


def _catalog(aln: Alignment, catalog: Path | None) -> str:
    if catalog:
        catalog_path = expand_path(catalog)
        if not Path(catalog_path).is_file():
            raise WGSExtractError(f"Variant catalog not found: {catalog_path}")
        return catalog_path
    prefix = pixi_env_prefix("expansionhunter")
    name = {
        ("hg38", True): "hg38",
        ("hg38", False): "grch38",
        ("hg19", True): "hg19",
        ("hg19", False): "grch37",
    }[(aln.build, aln.chr_prefix)]
    path = (
        prefix / "share/ExpansionHunter/variant_catalog" / name / "variant_catalog.json"
        if prefix
        else None
    )
    if not (path and path.is_file()):
        raise WGSExtractError(
            "ExpansionHunter's variant catalog wasn't found (pixi install -e "
            "expansionhunter), or pass --catalog."
        )
    return str(path)


def _sex(aln: Alignment, sex: str | None) -> str:
    if sex:
        return sex
    inferred = sample_sex(aln.path, aln.outdir).lower()
    if inferred in ("male", "female"):
        logging.info(f"Sample sex: {inferred} (from chrX/chrY coverage)")
        return inferred
    warn(
        "sex_unknown",
        "Couldn't infer the sample's sex; treating it as female (two X "
        "alleles). Pass --sex if that's wrong.",
    )
    return "female"


def _format_alleles(call: RepeatCall) -> tuple[str, str]:
    return (
        "/".join(map(str, call.alleles)) or "-",
        "/".join(f"{low}-{high}" for low, high in call.intervals)
        if call.intervals
        else "-",
    )


def write_summary(path: str, calls: list[RepeatCall]) -> list[tuple[str, str]]:
    """A TSV of each pathogenic repeat's call and range; returns the
    (locus, line) of every one that isn't normal."""
    flagged = []
    with open(path, "w", newline="\n") as f:
        f.write(
            "locus\tdisease\tinheritance\trepeat_unit\tgenotype\tci\t"
            "normal_max\tpathogenic_min\tstatus\tcoverage\n"
        )
        for call in sorted(calls, key=lambda c: c.variant_id):
            threshold = THRESHOLDS.get(call.variant_id)
            known = threshold or Threshold("-", "-", 0, 0)
            if threshold is None and "_" in call.variant_id:
                continue  # A flanking repeat of a catalog locus.
            status = classify(call, threshold)
            genotype, ci = _format_alleles(call)
            f.write(
                "\t".join(
                    [
                        call.variant_id,
                        known.disease,
                        known.inheritance,
                        call.repeat_unit,
                        genotype,
                        ci,
                        str(known.benign_max)
                        if threshold and known.benign_max is not None
                        else "-",
                        str(known.pathogenic_min) if threshold else "-",
                        status,
                        f"{call.coverage:.1f}",
                    ]
                )
                + "\n"
            )
            if status not in ("normal", "-", "no call"):
                flagged.append(
                    (
                        call.variant_id,
                        f"{call.variant_id} ({known.disease}, {known.inheritance}): "
                        f"{call.repeat_unit} {genotype}"
                        + (f" [CI {ci}]" if call.intervals else "")
                        + f" {status}",
                    )
                )
    return flagged


def choose_caller(choice: str | None, platform: str | None) -> str:
    """``choice``, else LongTR for Nanopore and PacBio reads and
    ExpansionHunter for others."""
    long_reads = platform in LONG_READ_PLATFORMS
    if choice == "expansionhunter" and long_reads:
        raise UsageError(
            f"ExpansionHunter sizes repeats from short reads, and these are "
            f"{platform} reads. Use --caller longtr or --caller strkit."
        )
    if choice in LONG_READ_CALLERS and platform and not long_reads:
        raise UsageError(
            f"{TOOL_NAMES[choice]} sizes repeats from Nanopore or PacBio reads, "
            f"and these are {platform} reads. Use --caller expansionhunter."
        )
    return choice or ("longtr" if long_reads else "expansionhunter")


def _run_expansionhunter(
    aln: Alignment, reference: str, catalog: Path | None, sex: str, prefix: str,
    debug: bool,
) -> list[RepeatCall]:  # fmt: skip
    expansion_hunter = env_tool("ExpansionHunter", "expansionhunter")
    catalog_path = _catalog(aln, catalog)
    logging.info("Sizing repeat expansions with ExpansionHunter...")
    try:
        run_command(
            [*expansion_hunter, "--reads", aln.path, "--reference", reference,
             "--variant-catalog", catalog_path, "--output-prefix", prefix,
             "--sex", sex, "--threads", aln.threads],
            capture_output=not debug,
        )  # fmt: skip
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip().splitlines()[-1:] if e.stderr else []
        raise WGSExtractError(
            f"ExpansionHunter failed (exit {e.returncode})"
            + (f": {detail[0]}" if detail else ".")
        ) from e
    result = current_result()
    result.add_output(f"{prefix}.vcf", "repeat_calls")
    result.add_output(f"{prefix}.json", "expansionhunter_report")
    return parse_expansionhunter(f"{prefix}.json")


def _run_long_reads(
    aln: Alignment, reference: str, caller: str, catalog: Path | None, sex: str,
    prefix: str, platform: str | None,
) -> list[RepeatCall]:  # fmt: skip
    rows = long_read_repeats.catalog_rows(aln, caller, catalog)
    result = current_result()
    if caller == "longtr":
        vcf = long_read_repeats.run_longtr(aln, reference, rows, sex, prefix)
        result.add_output(vcf, "repeat_calls")
        calls = long_read_repeats.parse_longtr(vcf)
    else:
        vcf, report = long_read_repeats.run_strkit(
            aln, reference, rows, sex, prefix, platform
        )
        result.add_output(vcf, "repeat_calls")
        result.add_output(report, "strkit_report")
        calls = long_read_repeats.parse_strkit(report)
    return long_read_repeats.with_clipped_reads(
        aln, rows, calls, 1 if caller == "longtr" else 0
    )


def run_str(params: TypingStrParams, *, debug: bool = False) -> None:
    aln = open_alignment(params)
    reference = aln.require_reference()
    platform = input_platform(aln.path, reference)
    caller = choose_caller(params.caller, platform)
    if caller == "longtr" and long_read_repeats.is_r9(get_bam_header(aln.path) or ""):
        warn(
            "r9_reads",
            "These look like Nanopore R9.4.1 reads, and LongTR drops reads "
            "below mean quality 30, which leaves most R9 loci uncalled. "
            "--caller strkit handles R9 reads better.",
        )
    sex = _sex(aln, params.sex)
    tool = TOOL_NAMES[caller]

    prefix = str(Path(aln.outdir, f"{aln.stem}.str"))
    if caller == "expansionhunter":
        calls = _run_expansionhunter(aln, reference, params.catalog, sex, prefix, debug)
    else:
        calls = _run_long_reads(
            aln, reference, caller, params.catalog, sex, prefix, platform
        )

    summary = f"{prefix}.tsv"
    flagged = write_summary(summary, calls)
    classified = sum(1 for c in calls if c.variant_id in THRESHOLDS and c.alleles)
    logging.info(
        f"{classified - len(flagged)} of {classified} disease repeats are in the "
        "normal range."
    )
    result = current_result()
    result.set_reference(reference)
    result.add_output(summary, "repeat_summary")
    result.add_metrics(
        sex=sex,
        repeat_caller=caller,
        disease_repeats=classified,
        flagged_repeats=len(flagged),
    )
    for locus, line in flagged:
        logging.warning(f"  {line}")
        result.warn("repeat_outside_normal_range", line, locus=locus)
    if flagged:
        logging.warning(
            "Repeat sizing from sequencing reads isn't a diagnosis; confirm "
            "anything outside the normal range with a clinical test."
        )
    logging.info(f"Repeat calls: {summary} ({tool}: {prefix}.*)")


cmd_str = cli_handler(TypingStrParams, run_str, debug="debug")
