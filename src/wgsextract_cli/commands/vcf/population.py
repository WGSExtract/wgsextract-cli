import logging
import re
import subprocess
import tempfile
from pathlib import Path

from wgsextract_cli.api.vcf import VcfGnomadParams, VcfPhylopParams
from wgsextract_cli.commands._messages.vcf import LOG_MESSAGES
from wgsextract_cli.core import variant_confidence
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.gnomad_af import (
    GNOMAD_AF_TABLE,
    GNOMAD_V4_REBUILD,
    gnomad_table_header,
)
from wgsextract_cli.core.output_checks import vcf_record_count
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import ensure_vcf_prepared, is_vcf_path
from wgsextract_cli.core.variant_table import parse_header

from .annotation_helpers import (
    annotate_vcf,
    annotation_context,
    prepare_tabix_annotation,
    record_annotated,
    require_annotation_file,
    resource,
    run_min_score_filter,
)
from .basic import vcf_input_handler
from .bigwig_scores import annotate_from_bigwig

PHYLOP_ANNOTATED = "phylop_annotated.vcf.gz"
GNOMAD_ANNOTATED = "gnomad_annotated.vcf.gz"
_GNOMAD_TAGS = {
    "GNOMAD_AF": ("Float", "gnomAD Allele Frequency"),
    "GNOMAD_POPMAX_AF": ("Float", "gnomAD maximum genetic ancestry group frequency"),
    "GNOMAD_NHOMALT": (
        "Integer",
        "gnomAD count of homozygous individuals for this ALT",
    ),
}


def run_phylop(params: VcfPhylopParams) -> None:
    verify_dependencies(["bcftools", "tabix", "bgzip"])
    log_dependency_info(["bcftools", "tabix", "bgzip"])
    input_file, outdir, lib = annotation_context(params, PHYLOP_ANNOTATED)
    logging.info(LOG_MESSAGES["vcf_phylop_start"].format(input=input_file))

    phylop_file = require_annotation_file(
        resource(params, "phylop_file", lib.phylop_file), "vcf_phylop_missing", "phylop"
    )
    logging.info(LOG_MESSAGES["vcf_phylop_resolve"].format(path=phylop_file))

    ann_out = str(Path(outdir, PHYLOP_ANNOTATED))
    if phylop_file.lower().endswith((".bw", ".bigwig")):
        # `ref phylop`'s UCSC bigWig: one score per base.
        annotate_from_bigwig(
            ensure_vcf_prepared(input_file),
            phylop_file,
            "PHYLOP",
            "PhyloP",
            "PhyloP 100-way vertebrate conservation score",
            ann_out,
            outdir,
            threads=params.threads,
        )
    else:
        _annotate_from_phylop_table(
            params, input_file, phylop_file, ann_out, outdir, lib.fasta
        )

    run_min_score_filter(
        ann_out,
        outdir,
        params.min_score,
        "PHYLOP",
        "phylop",
        "PhyloP",
        "vcf_phylop_done",
    )


def _annotate_from_phylop_table(
    params: VcfPhylopParams,
    input_file: str,
    phylop_file: str,
    ann_out: str,
    outdir: str,
    reference: str | None = None,
) -> None:
    """A PhyloP VCF, or a TSV of #Chr, Start, End, Score, the user supplied."""
    phylop_vcf = prepare_tabix_annotation(phylop_file, "PhyloP")
    if is_vcf_path(phylop_vcf, bcf=False):
        annotate_vcf(
            ensure_vcf_prepared(input_file),
            phylop_vcf,
            "INFO/PHYLOP",
            ann_out,
            outdir,
            "PhyloP",
            threads=params.threads,
            reference=reference,
            annotation_source=phylop_file,
        )
    else:
        # Annovar PhyloP TSV: #Chr, Start, End, Score
        annotate_vcf(
            ensure_vcf_prepared(input_file),
            phylop_vcf,
            "CHROM,POS,-,INFO/PHYLOP",
            ann_out,
            outdir,
            "PhyloP",
            [
                "##INFO=<ID=PHYLOP,Number=1,Type=Float,"
                'Description="PhyloP conservation score">'
            ],
            threads=params.threads,
        )


def _gnomad_vcf_columns(path: str) -> tuple[list[str], list[str]]:
    text = run_command(["bcftools", "view", "-h", path], capture_output=True).stdout
    tags = parse_header(text.splitlines()).info_number
    sources = {
        "GNOMAD_AF": ("AF_joint", "AF"),
        "GNOMAD_POPMAX_AF": ("AF_grpmax_joint", "AF_grpmax", "AF_popmax"),
        "GNOMAD_NHOMALT": ("nhomalt_joint", "nhomalt"),
    }
    columns, destinations = [], []
    for dest, names in sources.items():
        source = next((name for name in names if name in tags), None)
        if source is None and dest == "GNOMAD_AF":
            source = "AF"  # Preserve the error for a VCF without frequency.
        if source:
            columns.append(f"INFO/{dest}:=INFO/{source}")
            destinations.append(dest)
    return columns, destinations


def _gnomad_table_columns(fields: tuple[str, ...]) -> tuple[list[str], list[str]]:
    columns = ["CHROM", "POS", "REF", "ALT", "INFO/GNOMAD_AF"]
    destinations = ["GNOMAD_AF"]
    extras = {"AF_grpmax_joint": "GNOMAD_POPMAX_AF", "nhomalt_joint": "GNOMAD_NHOMALT"}
    for name in fields[5:]:
        if dest := extras.get(name):
            destinations.append(dest)
            columns.append(f"INFO/{dest}")
        else:
            columns.append("-")
    return columns, destinations


def run_gnomad(params: VcfGnomadParams) -> None:
    verify_dependencies(["bcftools", "tabix"])
    log_dependency_info(["bcftools", "tabix"])
    input_file, outdir, lib = annotation_context(params, GNOMAD_ANNOTATED)
    logging.info(f"Annotating VCF with gnomAD data: {input_file}")

    gnomad_file = require_annotation_file(
        resource(params, "gnomad_file", lib.gnomad_vcf),
        "vcf_gnomad_missing",
        "gnomad",
        install_args="--download",
    )
    logging.info(f"Using gnomAD file: {gnomad_file}")

    # Transfer AF as GNOMAD_AF to avoid colliding with the caller's AF.
    ann_out = str(Path(outdir, GNOMAD_ANNOTATED))
    if is_vcf_path(gnomad_file, bcf=False):
        annotation = ensure_vcf_prepared(gnomad_file)
        columns, destinations = _gnomad_vcf_columns(annotation)
    else:
        annotation = prepare_tabix_annotation(gnomad_file, "gnomAD")
        table_header = gnomad_table_header(annotation)
        columns, destinations = _gnomad_table_columns(table_header.columns)
        if (
            table_header.joint or Path(gnomad_file).name == GNOMAD_AF_TABLE
        ) and not table_header.is_current:
            warn(
                "gnomad_table_stale",
                "The gnomAD v4.1 table is not in the current per-allele format. "
                f"To rebuild, run: {GNOMAD_V4_REBUILD}",
                path=gnomad_file,
            )
    header = [
        f'##INFO=<ID={dest},Number=A,Type={kind},Description="{description}">'
        for dest in destinations
        for kind, description in [_GNOMAD_TAGS[dest]]
    ]
    annotate_vcf(
        ensure_vcf_prepared(input_file),
        annotation,
        ",".join(columns),
        ann_out,
        outdir,
        "gnomAD",
        header,
        threads=params.threads,
        remove=[f"INFO/{tag}" for tag in _GNOMAD_TAGS if tag not in destinations],
        reference=lib.fasta,
        annotation_source=gnomad_file,
    )

    record_annotated(ann_out)
    if params.max_af is None:
        logging.info(LOG_MESSAGES["vcf_gnomad_done"].format(output=ann_out))
        return

    filter_out = str(Path(outdir, f"gnomad_af_lt_{params.max_af}.vcf.gz"))
    logging.info(
        f"Filtering for variants with gnomAD Allele Frequency < {params.max_af} "
        f"to {filter_out}"
    )
    # Rare-variant filtering keeps variants absent from gnomAD too.
    try:
        summary = variant_confidence.label_vcf(
            ann_out,
            filter_out,
            variant_confidence.thresholds_from(params),
            include=f"GNOMAD_AF < {params.max_af} || GNOMAD_AF='.'",
            summary_columns=destinations,
            samples=(input_file,),
        )
        with tempfile.TemporaryDirectory(prefix=".wgse_evidence_", dir=outdir) as work:
            variant_confidence.add_reference_evidence(
                summary, input_file, work, reference=lib.fasta
            )
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"gnomAD filtering failed: {e}") from e
    table = re.sub(r"\.vcf\.gz$", ".confidence.tsv", filter_out)
    variant_confidence.write_table(
        table, summary, destinations, f"Calls with gnomAD AF < {params.max_af}"
    )
    variant_confidence.record(
        summary,
        output=filter_out,
        role="rare_variants",
        what=f"calls rarer than gnomAD AF {params.max_af}",
        table=table,
    )
    count = vcf_record_count(filter_out)
    current_result().add_output(
        filter_out, "rare_variants", metrics={"variants": count}
    )
    current_result().add_metrics(filtered_variants=count)
    logging.info(f"gnomAD filtering complete: {filter_out}")


cmd_phylop = vcf_input_handler(VcfPhylopParams, run_phylop)
cmd_gnomad = vcf_input_handler(VcfGnomadParams, run_gnomad)
