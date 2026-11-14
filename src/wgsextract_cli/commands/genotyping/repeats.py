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
from wgsextract_cli.core.sequencing import input_platform, refuse_mixed_platforms
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


# An allele read in fewer reads than this, and in under ALLELE_FRACTION_MIN of
# the locus's reads, is "low support": LongTR makes alleles of single reads
# (HG002's DMPK 11/19, truth 11/11). True expansions can be thinly read, so
# both limits are low: of the PacBio PureTarget expansions of #615, STRkit read
# NA03697B2's DMPK expansion in 2 of 247 reads and LongTR NA15850L1's FXN in 1
# of 1 (out/615/support.md).
ALLELE_READS_MIN = 2
ALLELE_FRACTION_MIN = 0.10


def low_support(call: RepeatCall) -> tuple[bool, ...]:
    """Per allele, whether too few reads support it (all False when the
    caller doesn't count them)."""
    if not call.allele_reads:
        return tuple(False for _ in call.alleles)
    return tuple(
        reads < ALLELE_READS_MIN and reads < ALLELE_FRACTION_MIN * call.coverage
        for reads in call.allele_reads
    )


def _grade(alleles: list[int], copies: int, threshold: Threshold) -> str:
    expanded = sum(a >= threshold.pathogenic_min for a in alleles)
    recessive = threshold.inheritance == "AR" or (
        threshold.inheritance == "XR" and copies > 1
    )
    if expanded and recessive and expanded < copies:
        return "carrier"
    if expanded:
        return "pathogenic"
    if threshold.premutation_min is not None and any(
        a >= threshold.premutation_min for a in alleles
    ):
        return "premutation"
    if threshold.benign_max is not None and any(
        a > threshold.benign_max for a in alleles
    ):
        return "intermediate"
    return "normal"


def _expected_copies(threshold: Threshold, sex: str) -> int:
    return 1 if threshold.inheritance in ("XR", "XD") and sex == "male" else 2


def unconfirmed(
    call: RepeatCall, threshold: Threshold | None, *, sex: str = "female"
) -> str:
    """A note when low-support alleles alone would change the grade, else ""."""
    weak = low_support(call)
    if threshold is None or not any(weak):
        return ""
    if all(weak):
        return " (unconfirmed: all called alleles have low read support)"
    copies = _expected_copies(threshold, sex)
    confirmed = [a for a, w in zip(call.alleles, weak, strict=True) if not w]
    status = _grade(list(call.alleles), copies, threshold)
    if status == _grade(confirmed, copies, threshold):
        return ""
    allele, reads = next(
        (a, n)
        for a, n, w in zip(call.alleles, call.allele_reads or (), weak, strict=True)
        if w
    )
    return (
        f" (unconfirmed: {'one read suggests' if reads == 1 else 'no read supports'}"
        f" an allele of {allele}, {status})"
    )


def classify(
    call: RepeatCall, threshold: Threshold | None, *, sex: str = "female"
) -> str:
    """normal, intermediate, premutation, carrier, pathogenic or unresolved
    (or "-" without thresholds), with a note when an allele's confidence
    interval reaches the pathogenic range but its call doesn't. Low-support
    alleles (``low_support``) don't count towards the grade; a note says what
    they suggest."""
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
    note = unconfirmed(call, threshold, sex=sex)
    alleles = [a for a, w in zip(call.alleles, low_support(call), strict=True) if not w]
    copies = _expected_copies(threshold, sex)
    status = _grade(alleles, copies, threshold) if alleles else "unresolved"
    if len(call.alleles) < copies:
        note += f" (incomplete call: {len(call.alleles)} of {copies} expected alleles)"
        if status != "pathogenic":
            status = "unresolved"
    expanded = status in ("carrier", "pathogenic")
    if status != "pathogenic" and any(
        allele < threshold.pathogenic_min <= high
        for allele, (_low, high) in zip(
            call.alleles, call.intervals or (), strict=False
        )
    ):
        status += " (CI reaches pathogenic)"
    if expanded and call.variant_id in MOTIF_UNCHECKED:
        status = f"{status}-length expansion, motif unchecked"
    return status + clipped + note


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


def _format_reads(call: RepeatCall) -> str:
    if not call.allele_reads:
        return "-"
    weak = any(low_support(call))
    return "/".join(map(str, call.allele_reads)) + (" (low support)" if weak else "")


def write_summary(
    path: str,
    calls: list[RepeatCall],
    unavailable: dict[str, str] | None = None,
    *,
    sex: str = "female",
) -> list[tuple[str, str, str]]:
    """A TSV of each pathogenic repeat's call and range, with a row for each
    repeat of ``unavailable`` (locus: why); returns the (warning code, locus,
    line) of every one that isn't normal or rests on low-support alleles."""
    flagged = []
    unavailable = unavailable or {}
    rows = {call.variant_id: call for call in calls}
    for locus in unavailable:
        rows.setdefault(locus, RepeatCall(locus, "-", "", (), None, 0.0))
    with open(path, "w", newline="\n") as f:
        f.write(
            "locus\tdisease\tinheritance\trepeat_unit\tgenotype\tci\t"
            "normal_max\tpathogenic_min\tstatus\tcoverage\tallele_reads\n"
        )
        for variant_id in sorted(rows):
            call = rows[variant_id]
            threshold = THRESHOLDS.get(call.variant_id)
            known = threshold or Threshold("-", "-", 0, 0)
            if threshold is None and call.variant_id.split("_")[0] in THRESHOLDS:
                continue  # A flanking repeat of a catalog locus.
            why = unavailable.get(variant_id, "")
            status = why or classify(call, threshold, sex=sex)
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
                        _format_reads(call),
                    ]
                )
                + "\n"
            )
            line = (
                f"{call.variant_id} ({known.disease}, {known.inheritance}): "
                f"{call.repeat_unit} {genotype}"
                + (f" [CI {ci}]" if call.intervals else "")
                + f" {status}"
            )
            note = unconfirmed(call, threshold, sex=sex)
            if why:
                line = f"{variant_id} ({known.disease}, {known.inheritance}): {why}"
                flagged.append(("repeat_unavailable", variant_id, line))
            elif status.startswith("unresolved"):
                flagged.append(("repeat_unconfirmed", variant_id, line))
                if call.clipped_reads:
                    flagged.append(("repeat_outside_normal_range", variant_id, line))
            elif status.removesuffix(note) not in ("normal", "-", "no call"):
                flagged.append(("repeat_outside_normal_range", variant_id, line))
            elif note:
                flagged.append(("repeat_unconfirmed", variant_id, line))
    return flagged


def catalog_loci(aln: Alignment, caller: str) -> set[str]:
    """The repeats of the bundled (or ExpansionHunter's shipped) catalog for
    the input's build."""
    if caller != "expansionhunter":
        rows = long_read_repeats.catalog_rows(aln, caller, None)
        if caller == "longtr":
            return {row.rsplit("\t", 1)[-1] for row in rows}
        return {row.split("ID=")[1].split(";")[0] for row in rows}
    with open(_catalog(aln, None)) as f:
        return {
            variant_id
            for locus in json.load(f)
            for variant_id in locus.get("VariantId") or [locus["LocusId"]]
        }


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
    if not params.caller and platform is None:
        refuse_mixed_platforms(aln.path, reference, "--caller")
    caller = choose_caller(params.caller, platform)
    if caller == "longtr" and long_read_repeats.is_r9(get_bam_header(aln.path) or ""):
        warn(
            "r9_reads",
            "These look like Nanopore R9.4.1 reads, and LongTR drops reads "
            "below mean quality 30, which leaves most R9 loci uncalled. "
            "--caller strkit handles R9 reads better.",
        )
    # Fail on a missing environment or catalog before minutes of sex inference.
    if caller == "expansionhunter":
        env_tool("ExpansionHunter", "expansionhunter")
        _catalog(aln, params.catalog)
    else:
        env_tool({"longtr": "LongTR", "strkit": "strkit"}[caller], caller)
        long_read_repeats.catalog_rows(aln, caller, params.catalog)
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
    unavailable = {}
    if params.catalog is None:
        catalog_ids = {c.variant_id for c in calls} | catalog_loci(aln, caller)
        unavailable = dict.fromkeys(
            sorted(set(THRESHOLDS) - catalog_ids),
            f"not available on {aln.build}: the bundled catalog lacks it",
        )
    warnings = write_summary(summary, calls, unavailable, sex=sex)
    flagged = [w for w in warnings if w[0] == "repeat_outside_normal_range"]
    classified = sum(1 for c in calls if c.variant_id in THRESHOLDS and c.alleles)
    uncertain = sum(w[0] == "repeat_unconfirmed" for w in warnings)
    non_normal = {locus for code, locus, _ in warnings if code != "repeat_unavailable"}
    normal = sum(
        1
        for c in calls
        if c.variant_id in THRESHOLDS and c.alleles and c.variant_id not in non_normal
    )
    logging.info(f"{normal} of {classified} disease repeats are in the normal range.")
    result = current_result()
    result.set_reference(reference)
    result.add_output(summary, "repeat_summary")
    result.add_metrics(
        sex=sex,
        repeat_caller=caller,
        disease_repeats=classified,
        flagged_repeats=len(flagged),
        unconfirmed_repeats=uncertain,
    )
    for code, locus, line in warnings:
        logging.warning(f"  {line}")
        result.warn(code, line, locus=locus)
    if flagged:
        logging.warning(
            "Repeat sizing from sequencing reads isn't a diagnosis; confirm "
            "anything outside the normal range with a clinical test."
        )
    logging.info(f"Repeat calls: {summary} ({tool}: {prefix}.*)")


cmd_str = cli_handler(TypingStrParams, run_str, debug="debug")
