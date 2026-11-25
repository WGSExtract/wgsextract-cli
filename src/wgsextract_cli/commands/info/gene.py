"""`info gene`: one gene's span, transcripts, variants and exon coverage."""

from __future__ import annotations

import re
from pathlib import Path

from wgsextract_cli.api._cli import cli_handler
from wgsextract_cli.api.info import InfoGeneParams
from wgsextract_cli.core import acmg
from wgsextract_cli.core.config import typed_settings
from wgsextract_cli.core.gene_summary import (
    FILTERS,
    GeneQuery,
    GeneSummary,
    summarise_gene,
    variant_filters,
)
from wgsextract_cli.core.ref_library.assets import is_fasta_path
from wgsextract_cli.core.ref_library.resolver import library_roots
from wgsextract_cli.core.results import current_result, human_output, warn
from wgsextract_cli.core.table_writer import TableWriter, provenance
from wgsextract_cli.core.variant_table import COLUMN_NAMES, COLUMNS

_VCF_SUFFIX = re.compile(r"\.(g?vcf(\.b?gz)?|bcf)$", re.IGNORECASE)


def _export(params: InfoGeneParams, summary: GeneSummary) -> None:
    assert params.export and params.vcf_input and summary.variants
    choice = params.export.replace("-", "_")
    annotations = summary.variants.header.annotations
    rows = [
        values
        for values in summary.variants.rows
        if choice == "all"
        or choice
        in variant_filters(dict(zip(COLUMN_NAMES, values, strict=True)), annotations)
    ]
    gene = summary.document["gene"]
    assert isinstance(gene, dict)
    stem = _VCF_SUFFIX.sub("", Path(params.vcf_input).name)
    outdir = Path(params.outdir or Path(params.vcf_input).parent)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{stem}_{gene['symbol']}_{params.export}_variants.tsv"
    metadata = provenance(
        [params.vcf_input],
        reference_build=str(summary.document["build"]),
        sorted_by="the VCF's record order (chrom, pos)",
        gene=str(gene["symbol"]),
        region=str(gene["locus"]),
        filter=params.export,
        annotations=",".join(annotations),
        acmg_disclaimer=acmg.DISCLAIMER if "acmg" in annotations else None,
    )
    with TableWriter(str(path), COLUMNS, "tsv", metadata=metadata) as table:
        table.write(rows)
    current_result().add_output(path, "variant_table", metrics={"rows": len(rows)})
    human_output(f"{len(rows):,} {params.export} variant rows: {path}")


def run_gene(params: InfoGeneParams) -> None:
    reference = str(params.ref) if params.ref else None
    reflib = library_roots(
        reference if reference and not is_fasta_path(reference) else None
    ).root
    query = GeneQuery(
        symbol=params.gene,
        alignment=str(params.input) if params.input else None,
        vcf=str(params.vcf_input) if params.vcf_input else None,
        reference=reference,
        reflib=reflib,
        build=params.build,
        sample=params.sample,
        min_depth=(
            params.min_depth
            if params.min_depth is not None
            else typed_settings().confidence_min_depth
        ),
    )
    result = current_result()
    for path, kind in ((params.input, "alignment"), (params.vcf_input, "vcf")):
        if path:
            result.add_input(path, kind)
    summary = summarise_gene(query)
    document = summary.document
    result.set_details(document)
    for code, message in summary.warnings:
        warn(code, message)
    for notice in document["notices"]:  # type: ignore[attr-defined]
        warn(f"gene_{notice['code']}", notice["message"])

    gene = document["gene"]
    variants = document["variants"]
    coverage = document["coverage"]
    transcripts = document["transcripts"]
    assert isinstance(gene, dict) and isinstance(variants, dict)
    assert isinstance(coverage, dict) and isinstance(transcripts, dict)
    result.add_metrics(
        gene=str(gene["symbol"]),
        locus=str(gene["locus"]),
        reference_build=str(document["build"]),
        transcripts=len(transcripts["items"]),
        variants=int(variants.get("total") or 0),
        exons_below=int(coverage.get("exons_below") or 0),
    )
    human_output(f"{gene['symbol']} ({document['build']}) {gene['locus']}")
    human_output(f"  Transcripts: {len(transcripts['items'])}")
    if variants["status"] == "ok":
        counts = {
            name: sum(name in row["filters"] for row in variants["rows"])
            for name in FILTERS
        }
        human_output(
            f"  Variants: {variants['total']:,} carried ("
            + ", ".join(f"{n.replace('_', ' ')}: {c}" for n, c in counts.items())
            + ")"
        )
    if coverage["status"] == "ok":
        stats = coverage["gene"]
        human_output(
            f"  Coverage: mean {stats['mean']}x, "
            f"{stats['callable_fraction']:.1%} of bases >= {coverage['threshold']:g}x; "
            f"{coverage['exons_below']} of {len(coverage['exons'])} exons below"
        )
    if params.export:
        _export(params, summary)


run = cli_handler(InfoGeneParams, run_gene)
