"""`vcf pharmcat`: star-allele pharmacogenomics with PharmCAT.

PharmCAT (https://pharmcat.org) calls diplotypes and metabolizer phenotypes
(CYP2C19 *17/*17, Ultrarapid Metabolizer, ...) for the CPIC/DPWG genes from a
GRCh38 VCF and reports the matching prescribing guidance. It isn't packaged for
conda, so the user extracts its release ("pharmcat-pipeline-<version>.tar.gz")
into <reference library>/pharmcat, or points pharmcat_directory / --pharmcat-dir
at it; the `pharmcat` Pixi environment supplies its Python, Java and bcftools.

PharmCAT takes neither gVCF reference blocks nor every FORMAT field a caller
writes, so the input is first cut to PharmCAT's gene regions, given its contig
names, expanded from blocks to positions, and reduced to genotypes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from wgsextract_cli.api.vcf import VcfPharmcatParams
from wgsextract_cli.core import runtime, variant_confidence
from wgsextract_cli.core.builds import build_from_chr1_length, build_from_path
from wgsextract_cli.core.dependency_checks import verify_dependencies
from wgsextract_cli.core.paths import absolute_path
from wgsextract_cli.core.pixi_tools import pixi_env_tool
from wgsextract_cli.core.reference_resolver import (
    ReferenceLibrary,
    header_contig_lengths,
)
from wgsextract_cli.core.results import current_result, warn
from wgsextract_cli.core.tool_commands import _tool_command_parts
from wgsextract_cli.core.utils import WGSExtractError, run_command
from wgsextract_cli.core.variant_files import (
    chromosome_rename_mapping,
    ensure_vcf_indexed,
    ensure_vcf_prepared,
    vcf_index_chromosomes,
)

from .basic import require_vcf_input, vcf_input_handler
from .pharmcat_evidence import expand

RELEASES_URL = "https://github.com/PharmGKB/PharmCAT/releases"
PIPELINE_FILES = ("pharmcat_pipeline", "pharmcat.jar", "pharmcat_regions.bed")
# Shipped with the release's preprocessor; fetched by PharmCAT on first use.
REFERENCE_FASTA = "reference.fna.bgz"


def pharmcat_directory(
    args: VcfPharmcatParams | argparse.Namespace, lib: ReferenceLibrary
) -> str:
    """The extracted PharmCAT pipeline: --pharmcat-dir, pharmcat_directory, or
    <reference library>/pharmcat."""
    directory = args.pharmcat_dir or (
        str(Path(root, "pharmcat")) if (root := lib.resource_root("pharmcat")) else None
    )
    missing = [
        name
        for name in PIPELINE_FILES
        if not directory or not Path(directory, name).is_file()
    ]
    if missing or directory is None:
        raise WGSExtractError(
            f"PharmCAT not found in {directory or '(no reference library)'} "
            f"(missing {', '.join(missing)}). Download pharmcat-pipeline-<version>"
            f".tar.gz from {RELEASES_URL}, extract it there (or set "
            "pharmcat_directory in config.toml, or pass --pharmcat-dir)."
        )
    return str(absolute_path(directory))


def _python(directory: str) -> list[str]:
    command = pixi_env_tool("pharmcat_python")
    if not command:
        raise WGSExtractError(
            "PharmCAT's environment isn't installed: pixi install --locked -e pharmcat"
        )
    return _tool_command_parts(command)


def _ensure_reference(python: list[str], directory: str) -> str:
    reference = str(Path(directory, REFERENCE_FASTA))
    if not Path(reference).is_file():
        logging.info("Fetching PharmCAT's GRCh38 reference (about 1 GB, once)...")
        fetch = (
            "import sys; sys.path.insert(0, sys.argv[1]); import pcat; "
            "pcat.download_reference_fasta_and_index(sys.argv[1])"
        )
        run_command([*python, "-c", fetch, directory])
    return reference


def prepare_input(
    input_vcf: str,
    directory: str,
    reference: str,
    work: str,
    params: VcfPharmcatParams | None = None,
) -> str:
    """input_vcf cut to PharmCAT's regions, in its contig names, with gVCF
    blocks expanded to positions and only GT kept."""
    regions = str(Path(directory, "pharmcat_regions.bed"))
    with open(regions) as f:
        bed_lines = [line.split("\t") for line in f if line.strip()]
    pharmcat_chroms = sorted({fields[0] for fields in bed_lines})
    input_chroms = vcf_index_chromosomes(input_vcf)

    # The regions, in the input's names.
    to_input = dict(chromosome_rename_mapping(pharmcat_chroms, input_chroms))
    local_bed = str(Path(work, "regions.bed"))
    with open(local_bed, "w", newline="\n") as f:
        f.writelines(
            "\t".join([to_input.get(fields[0], fields[0]), *fields[1:]])
            for fields in bed_lines
        )
    subset = str(Path(work, "regions.vcf.gz"))
    run_command(["bcftools", "view", "-R", local_bed, "-Oz", "-o", subset, input_vcf])
    ensure_vcf_indexed(subset)

    renamed = subset
    subset_header = run_command(
        ["bcftools", "view", "-h", subset], capture_output=True
    ).stdout
    to_pharmcat = chromosome_rename_mapping(
        list(header_contig_lengths(subset_header)), pharmcat_chroms
    )
    if to_pharmcat:
        names = str(Path(work, "names.txt"))
        with open(names, "w", newline="\n") as f:
            f.writelines(f"{source} {target}\n" for source, target in to_pharmcat)
        renamed = str(Path(work, "renamed.vcf.gz"))
        run_command(["bcftools", "annotate", "--rename-chrs", names, "-Oz", "-o",
                     renamed, subset])  # fmt: skip
        ensure_vcf_indexed(renamed)

    prepared = str(Path(work, "prepared.vcf.gz"))
    expanded = expand(
        renamed,
        directory,
        reference,
        work,
        [(f[0], int(f[1]), int(f[2])) for f in bed_lines],
        variant_confidence.thresholds_from(params),
    )
    # GATK/DRAGEN gVCFs keep ##GVCFBlock lines after expansion, and PharmCAT's
    # pipeline refuses any VCF that has them as a gVCF.
    header = run_command(["bcftools", "view", "-h", expanded], capture_output=True)
    header_file = str(Path(work, "header.txt"))
    with open(header_file, "w", newline="\n") as f:
        f.writelines(
            line + "\n"
            for line in header.stdout.splitlines()
            if not line.startswith("##GVCFBlock")
        )
    reheadered = str(Path(work, "reheadered.vcf.gz"))
    run_command(["bcftools", "reheader", "-h", header_file, "-o", reheadered, expanded])
    run_command(["bcftools", "annotate", "-x", "INFO,^FORMAT/GT", "-Oz", "-o",
                 prepared, reheadered])  # fmt: skip
    ensure_vcf_indexed(prepared)
    return prepared


def label_pgx_calls(
    input_vcf: str, work: str, params: VcfPharmcatParams
) -> variant_confidence.ConfidenceSummary:
    """Confidence labels for the input's calls in PharmCAT's gene regions
    (written by prepare_input to work/regions.bed, in the input's names): a
    diplotype is no better than the calls it rests on."""
    regions = str(Path(work, "regions.bed"))
    named: list[tuple[str, int, int, str]] = []
    with open(regions) as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 3:
                name = fields[3].rpartition("=")[2] if len(fields) > 3 else ""
                named.append((fields[0], int(fields[1]), int(fields[2]), name))
    # evidence.vcf uses PharmCAT's names, rather than the source's names.
    evidence = str(Path(work, "evidence.vcf"))
    header = run_command(
        ["bcftools", "view", "-h", evidence], capture_output=True
    ).stdout
    chroms = list(header_contig_lengths(header))
    mapping = dict(chromosome_rename_mapping([n[0] for n in named], chroms))
    return variant_confidence.label_vcf(
        evidence,
        None,
        variant_confidence.thresholds_from(params),
        region_names=[(mapping.get(c, c), s, e, n) for c, s, e, n in named],
        summary_columns=("PGX_STATE",),
        include_reference=True,
        samples=(input_vcf,),
        suppress_rare_imputed=False,
    )


def merge_outside_calls(paths: Sequence[str | Path], merged: str) -> str:
    """One PharmCAT outside-call file from several (e.g. `typing cyp2d6` and
    `typing hla` output); each gene may be called by only one of them."""
    seen: dict[str, str] = {}
    lines = []
    for path in paths:
        if not Path(path).is_file():
            raise WGSExtractError(f"Outside-call file not found: {path}")
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                gene = line.split("\t", 1)[0].strip()
                if gene in seen:
                    raise WGSExtractError(
                        f"{gene} is called in both {seen[gene]} and {path}; "
                        "give PharmCAT one call per gene."
                    )
                seen[gene] = str(path)
                lines.append(line.rstrip("\r\n") + "\n")
    with open(merged, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    logging.info(f"Outside calls for PharmCAT: {', '.join(seen) or 'none'}")
    return merged


@dataclass
class GeneCall:
    gene: str
    status: str
    alternatives: list[dict[str, str]]
    phenotype_consistent: bool

    @property
    def diplotype(self) -> str:
        values = " OR ".join(a["diplotype"] for a in self.alternatives)
        return (
            f"Ambiguous: {values}"
            if self.status == "ambiguous"
            else values or "No call"
        )

    @property
    def phenotype(self) -> str:
        if self.status == "no_call":
            return "No call; no definitive guidance."
        if self.phenotype_consistent:
            return self.alternatives[0]["phenotype"]
        return (
            "Conflicting or unknown phenotypes; no definitive guidance. "
            + "; ".join(
                f"{a['diplotype']}: {a['phenotype']}" for a in self.alternatives
            )
        )


def summarize(phenotype_json: str) -> list[GeneCall]:
    """One gene result, preserving alternative diplotypes and explicit no-calls."""
    with open(phenotype_json) as f:
        reports = json.load(f).get("geneReports", {})
    rows: list[GeneCall] = []
    for gene, report in sorted(reports.items()):
        alternatives: list[dict[str, str]] = []
        unknown = False
        for diplotype in report.get("recommendationDiplotypes") or []:
            alleles = [
                (diplotype.get(side) or {}).get("name") or "?"
                for side in ("allele1", "allele2")
            ]
            unknown |= any(
                a.lower() in {"unknown", "?", "no call", "no-call"} for a in alleles
            )
            phenotype = ", ".join(diplotype.get("phenotypes") or []) or "-"
            alternative = {"diplotype": "/".join(alleles), "phenotype": phenotype}
            if alternative not in alternatives:
                alternatives.append(alternative)
        status = (
            "ambiguous"
            if len(alternatives) > 1
            else "no_call"
            if unknown or not alternatives
            else "resolved"
        )
        consistent = (
            bool(alternatives)
            and not unknown
            and (
                len({a["phenotype"] for a in alternatives}) == 1
                and alternatives[0]["phenotype"].lower()
                not in {"-", "unknown", "no call", "n/a"}
            )
        )
        rows.append(GeneCall(gene, status, alternatives, consistent))
    return rows


def _vcf_build(vcf: str) -> str | None:
    try:
        header = run_command(["bcftools", "view", "-h", vcf], capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    by_length = build_from_chr1_length(header_contig_lengths(header.stdout))
    if by_length:
        return by_length
    for line in header.stdout.splitlines():
        if line.startswith("##reference="):
            return build_from_path(line.partition("=")[2])
    return None


def run_pharmcat(params: VcfPharmcatParams) -> None:
    verify_dependencies(["bcftools", "tabix", "samtools"])
    input_vcf = require_vcf_input(params)
    outdir = str(absolute_path(params.outdir or Path(input_vcf).parent))
    Path(outdir).mkdir(parents=True, exist_ok=True)
    lib = ReferenceLibrary(params.ref, None, input_path=input_vcf)
    # The library knows the build only when there is one; the VCF's contigs
    # tell it either way.
    build = lib.build or _vcf_build(input_vcf)
    if build and "38" not in build:
        raise WGSExtractError(f"PharmCAT needs a GRCh38 VCF; {input_vcf} is {build}.")
    directory = pharmcat_directory(params, lib)
    python = _python(directory)
    input_vcf = ensure_vcf_prepared(input_vcf)  # bgzipped and indexed
    reference = _ensure_reference(python, directory)
    stem = re.sub(r"\.(vcf(\.b?gz)?|bcf)$", "", Path(input_vcf).name, flags=re.I)

    with tempfile.TemporaryDirectory(dir=outdir, prefix=".wgse_pharmcat_") as work:
        logging.info("Preparing the VCF for PharmCAT (its gene regions only)...")
        prepared = prepare_input(input_vcf, directory, reference, work, params)
        confidence = label_pgx_calls(input_vcf, work, params)
        run_out = Path(work, "outputs")
        run_out.mkdir()
        command = [
            *python, str(Path(directory, "pharmcat_pipeline")), prepared,
            "-o", str(run_out), "-bf", stem, "-reporterHtml", "-reporterJson",
        ]  # fmt: skip
        if params.absent_to_ref:
            command.append("--absent-to-ref")
        if runtime.is_windows_host():
            # PharmCAT 3.4.0 compares the unset process count with Windows'
            # limit of 61 and fails; pass its own default, within that limit.
            cpus = os.cpu_count() or 1
            command += ["-cp", str(1 if cpus < 3 else min(61, max(2, cpus - 2)))]
        # The pipeline has no outside-call flag: it uses `<base name>.outside.tsv`
        # beside its preprocessed VCF, which it writes to the output directory.
        outside = str(run_out / f"{stem}.outside.tsv")
        if params.outside_call:
            merge_outside_calls(params.outside_call, outside)
            for path in params.outside_call:
                current_result().add_input(path, "pharmcat_outside_call")
        used_outside_calls = bool(params.outside_call)
        logging.info("Running PharmCAT...")
        try:
            # PharmCAT writes pharmcat.log to its working directory.
            run_command(command, cwd=outdir)
        except subprocess.CalledProcessError as e:
            raise WGSExtractError(f"PharmCAT failed (exit {e.returncode}).") from e
        try:
            rows = summarize(str(run_out / f"{stem}.phenotype.json"))
        except (OSError, ValueError) as e:
            raise WGSExtractError(
                f"PharmCAT did not produce readable phenotypes: {e}"
            ) from e
        generated = {path.name for path in run_out.iterdir()}
        for path in run_out.iterdir():
            target = Path(outdir, path.name)
            if path.is_dir():
                shutil.copytree(path, target, dirs_exist_ok=True)
            else:
                os.replace(path, target)
        if used_outside_calls:
            current_result().add_output(
                Path(outdir, f"{stem}.outside.tsv"), "pharmcat_outside_calls"
            )

    report = str(Path(outdir, f"{stem}.report.html"))
    for suffix, role in (
        ("report.html", "pharmcat_report"),
        ("report.json", "pharmcat_report_json"),
        ("phenotype.json", "pharmcat_phenotypes"),
    ):
        if f"{stem}.{suffix}" in generated:
            current_result().add_output(Path(outdir, f"{stem}.{suffix}"), role)
    table = str(Path(outdir, f"{stem}.confidence.tsv"))
    variant_confidence.write_table(
        table, confidence, ("gene", "PGX_STATE"), "Calls in PharmCAT gene regions"
    )
    variant_confidence.record(
        confidence, output=None, role="pgx_calls", what="calls in PharmCAT genes",
        table=table,
    )  # fmt: skip
    result = current_result()
    result.set_details(
        {**(result.details or {}), "pharmcat_genes": [asdict(row) for row in rows]}
    )
    for status in ("resolved", "ambiguous", "no_call"):
        current_result().metrics[f"pharmcat_genes_{status}"] = sum(
            row.status == status for row in rows
        )
    for row in rows:
        logging.info(f"  {row.gene:<9} {row.diplotype:<22} {row.phenotype}")
    logging.info(f"PharmCAT report: {report}")
    if not used_outside_calls and runtime.is_windows_host():
        warn(
            "no_call",
            "CYP2D6 not called, nor HLA-A/HLA-B: PharmCAT can't call them from a "
            "VCF, and `typing cyp2d6` / `typing hla` don't run on Windows. Pass "
            "outside calls made elsewhere with --outside-call.",
            gene="CYP2D6",
        )
    elif not used_outside_calls:
        logging.info(
            "CYP2D6 and HLA-A/HLA-B need calls PharmCAT can't make from a VCF: "
            "pass them with --outside-call (from `typing cyp2d6` / `typing hla`)."
        )


cmd_pharmcat = vcf_input_handler(VcfPharmcatParams, run_pharmcat)
