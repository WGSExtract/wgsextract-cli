"""``vcf sv-freq``: population frequencies of SV calls from gnomAD-SV/gnomAD-LR."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from wgsextract_cli.api.vcf import VcfSvFreqParams
from wgsextract_cli.core import gnomad_sv
from wgsextract_cli.core import sv_frequency as svf
from wgsextract_cli.core.atomic_output import safe_output
from wgsextract_cli.core.builds import canonical_build
from wgsextract_cli.core.dependency_checks import (
    log_dependency_info,
    verify_dependencies,
)
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.table_writer import provenance, write_table
from wgsextract_cli.core.utils import UsageError, WGSExtractError, popen, run_command

from .annotation_helpers import annotation_context
from .basic import vcf_input_handler

SV_FREQ_ANNOTATED = "sv_freq_annotated.vcf.gz"
SV_FREQ_TABLE = "sv_freq.tsv"


def _tables(
    params: VcfSvFreqParams, root: str | None, build: str | None
) -> list[svf.SitesTable]:
    """The catalogues to search: for gnomAD-SV and for gnomAD-LR, the table
    given (or configured), else the library's verified one for the build."""
    given = {"sr": params.gnomad_sv_file, "lr": params.gnomad_lr_file}
    for path in filter(None, given.values()):
        if not Path(f"{path}.tbi").is_file():
            raise UsageError(
                f"{path} has no tabix index; give a table from 'wgsextract ref "
                "gnomad-sv'."
            )
    if build not in ("hg38", "hg19") and not any(given.values()):
        raise UsageError(
            f"gnomAD SV frequencies are for GRCh38 and GRCh37; this VCF's build is "
            f"{build or 'unknown'}. Give --gnomad-sv-file for another build."
        )
    library = {cat.source: path for path, cat in gnomad_sv.tables_for(root, build)}
    chosen = [given[s] or library.get(s) for s in gnomad_sv.SOURCES]
    tables = [svf.SitesTable(os.fspath(path)) for path in chosen if path]
    if not tables:
        raise WGSExtractError(
            f"REQUIRED DATA MISSING: no gnomAD SV table for {build} in the "
            f"reference library.\nTo fix this, run: wgsextract ref gnomad-sv "
            "--install"
        )
    if build == "hg38" and not (given["lr"] or "lr" in library):
        logging.info(
            "gnomAD-LR (long-read SVs) is not installed; 'wgsextract ref gnomad-sv "
            "--install --source lr' adds it."
        )
    return tables


def run_sv_freq(params: VcfSvFreqParams) -> None:
    verify_dependencies(["bcftools", "tabix", "bgzip"])
    log_dependency_info(["bcftools", "tabix", "bgzip"])
    input_file, outdir, lib = annotation_context(params)
    build = canonical_build(lib.build)
    tables = _tables(params, lib.root, build)
    rule = svf.MatchRule(
        params.min_overlap, params.max_distance, params.min_size_similarity
    )
    logging.info(
        f"Matching SVs in {input_file} against {', '.join(t.source for t in tables)}"
    )
    out_vcf = Path(outdir, SV_FREQ_ANNOTATED)
    summary = svf.Summary()
    with tempfile.TemporaryFile() as err, safe_output(out_vcf) as staged:
        with open(staged, "wb") as sink:
            reader = popen(
                ["bcftools", "view", input_file], stdout=subprocess.PIPE,
                stderr=err, text=True,
            )  # fmt: skip
            writer = popen(["bgzip", "-c"], stdin=subprocess.PIPE, stdout=sink)
            assert reader.stdout is not None and writer.stdin is not None
            try:
                for line in svf.annotate_lines(
                    reader.stdout,
                    tables,
                    rule,
                    params.common_af,
                    params.rare_af,
                    summary,
                ):
                    writer.stdin.write(line.encode())
            finally:
                writer.stdin.close()
            read_ok, write_ok = reader.wait() == 0, writer.wait() == 0
        if not (read_ok and write_ok):
            err.seek(0)
            detail = err.read().decode(errors="replace").strip()[-500:]
            raise WGSExtractError(f"Reading {input_file} failed: {detail}")
        run_command(["tabix", "-f", "-p", "vcf", staged])

    table = Path(outdir, SV_FREQ_TABLE)
    write_table(
        table,
        svf.TABLE,
        summary.rows,
        metadata=provenance([input_file], reference_build=build),
    )
    counts = {f"sv_{label}": summary.classes[label] for label in svf.CLASSES}
    fraction = summary.matched / summary.annotatable if summary.annotatable else 0.0
    result = current_result()
    result.add_output(out_vcf, "annotated_vcf", metrics={"variants": summary.records})
    result.add_output(table, "sv_frequencies", metrics={"svs": summary.annotatable})
    result.add_metrics(
        svs=summary.annotatable,
        sv_matched=summary.matched,
        sv_matched_fraction=round(fraction, 4),
        sources=",".join(t.source for t in tables),
        common_af=params.common_af,
        rare_af=params.rare_af,
        **counts,
    )
    if summary.records and not summary.annotatable:
        warn(
            "no_svs",
            f"{input_file} has no deletions, duplications, insertions, inversions "
            "or CNVs on contigs gnomAD covers; nothing was annotated.",
        )
    logging.info(
        f"{summary.matched:,} of {summary.annotatable:,} SVs ({fraction:.1%}) are "
        f"in gnomAD: {summary.classes[svf.COMMON]:,} common, "
        f"{summary.classes[svf.LOW]:,} low-frequency, "
        f"{summary.classes[svf.RARE]:,} rare; {summary.classes[svf.NOVEL]:,} not "
        f"found. Output: {out_vcf}"
    )


cmd_sv_freq = vcf_input_handler(VcfSvFreqParams, run_sv_freq)
