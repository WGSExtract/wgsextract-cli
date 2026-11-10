"""``extract region``: one region (chrM, chrY, both, or autosomes and X) as any
of a BAM, a VCF and a consensus FASTA."""

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.api.extract import ExtractRegionParams
from wgsextract_cli.core import ultima
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.atomic_output import atomic_outputs_enabled, safe_output
from wgsextract_cli.core.output_checks import warn_if_no_variants
from wgsextract_cli.core.reference_models import sequence_lengths
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.sample_ploidy import sex_samples_args
from wgsextract_cli.core.utils import WGSExtractError, run_pipeline
from wgsextract_cli.core.variant_files import ensure_vcf_indexed, get_chr_name

from ._extract_helpers import BaseArgs, cleanup_files, derive_output_path


@dataclass(frozen=True)
class _Target:
    label: str  # output name part: <input>_<label>.vcf.gz / .fasta
    role: str  # output role prefix: <role>_bam, <role>_vcf, <role>_fasta
    bam_suffix: str
    reads: str  # what the BAM holds, for the no-reads warning
    has_y: bool


_TARGETS = {
    "mt": _Target("MT", "mt", "_mtDNA.bam", "chrM reads", False),
    "y": _Target("Y", "y", "_Y.bam", "chrY reads", True),
    "ymt": _Target("Y_MT", "y_mt", "_Y_MT.bam", "chrY or chrM reads", True),
    "autox": _Target(
        "autosomes_X", "autox", "_autosomes_X.bam", "autosome or chrX reads", False
    ),
}
_AUTOSOMES_X = [*map(str, range(1, 23)), "X"]


def _contigs(target: str, input_path: str, cram_opt: list[str]) -> list[str]:
    """The target's contigs as the alignment names them."""
    if target == "mt":
        return [get_chr_name(input_path, "MT", cram_opt)]
    if target == "y":
        return [get_chr_name(input_path, "Y", cram_opt)]
    if target == "ymt":
        return [get_chr_name(input_path, n, cram_opt) for n in ("Y", "MT")]
    present = sequence_lengths(get_bam_header(input_path, cram_opt))
    contigs = [
        name
        for number in _AUTOSOMES_X
        for name in (f"chr{number}", number)
        if name in present
    ]
    if not contigs:
        raise WGSExtractError(
            f"{input_path} names no autosomes or chrX (1-22, X or chr1-chr22, chrX)."
        )
    return contigs


def run_region(params: ExtractRegionParams) -> None:
    from .extract import (
        _CALLING_TOOLS,
        _SAMTOOLS,
        _published,
        _setup,
        _warn_if_female,
        require_reference,
    )

    target = _TARGETS[params.target]
    keep_vcf = params.vcf and not params.ybrowse  # --ybrowse writes its own
    calls = params.fasta or keep_vcf
    tools = _CALLING_TOOLS if calls or params.ybrowse else _SAMTOOLS
    base = _setup(params, [*tools, "bgzip"] if params.ybrowse else tools)
    input_path = str(params.input)
    ref = require_reference(base.ref, "region calling", input_path) if calls else None
    contigs = _contigs(params.target, input_path, base.cram_opt)
    if target.has_y and not params.ybrowse:  # call_ybrowse warns itself
        _warn_if_female(input_path, base.outdir)

    def out(suffix: str) -> str:
        return derive_output_path(input_path, base.outdir, suffix)

    out_bam = out("_Y_poz.bam" if params.poz else target.bam_suffix)
    out_vcf = out(f"_{target.label}.vcf.gz")
    out_fasta = out(f"_{target.label}.fasta")
    outputs = [
        *([out_bam, out_bam + ".bai"] if params.bam else []),
        *([out_vcf, out_vcf + ".tbi"] if keep_vcf else []),
        *([out_fasta] if params.fasta else []),
    ]
    logging.info(
        f"Extracting {', '.join(contigs)} from {input_path} into {base.outdir}..."
    )
    try:
        with (
            cleanup_files(
                on_failure=outputs, preserve_outputs=atomic_outputs_enabled()
            ),
            tempfile.TemporaryDirectory(dir=base.outdir, prefix=".region_") as work,
        ):
            if params.bam:
                _write_bam(base, input_path, out_bam, contigs, target, params.poz)
                _published(out_bam, f"{target.role}_bam")
            if calls:
                assert ref is not None
                vcf = out_vcf if keep_vcf else str(Path(work, "calls.vcf.gz"))
                if params.target == "autox":
                    _call_diploid_snps(base, input_path, ref, contigs, vcf)
                else:
                    _call_haploid(base, input_path, ref, contigs, vcf)
                if keep_vcf:
                    _published(out_vcf, f"{target.role}_vcf")
                if params.fasta:
                    _consensus(ref, contigs, vcf, out_fasta, params.target == "autox")
                    _published(out_fasta, f"{target.role}_fasta")
    except (OSError, subprocess.SubprocessError, WGSExtractError) as e:
        raise WGSExtractError(f"Region extraction failed: {e}") from e
    if params.ybrowse:
        from ._extract_ybrowse import call_ybrowse

        call_ybrowse(input_path, base)


def _write_bam(
    base: BaseArgs,
    input_path: str,
    out_bam: str,
    contigs: list[str],
    target: _Target,
    poz: bool,
) -> None:
    from .extract import _extract_regions

    if not poz:
        hint = "a female sample has none" if target.has_y else ""
        _extract_regions(
            base, input_path, out_bam, contigs, contents=target.reads, empty_hint=hint
        )
        return
    from ._extract_poz import poz_bed

    lengths = sequence_lengths(get_bam_header(input_path, base.cram_opt))
    with poz_bed(lengths, contigs[0], base.outdir) as bed:
        _extract_regions(
            base,
            input_path,
            out_bam,
            contigs,
            bed=bed,
            contents=f"{contigs[0]} reads overlapping Poz regions",
            empty_hint="the Poz preset covers only selected Y regions",
        )


def _call_haploid(
    base: BaseArgs, input_path: str, ref: str, contigs: list[str], out_vcf: str
) -> None:
    """chrM / chrY variants, indels included, called haploid."""
    if not atomic_outputs_enabled():
        for stale in (out_vcf + ".tbi", out_vcf + ".csi"):
            Path(stale).unlink(missing_ok=True)
    logging.info(f"Calling {', '.join(contigs)} variants into {out_vcf}...")
    with safe_output(out_vcf) as staged:
        run_pipeline(
            [
                ["bcftools", "mpileup", "-Ou", "-f", ref, "-r", ",".join(contigs)]
                + ["--threads", base.threads, input_path],
                ["bcftools", "call", "--ploidy", "1", "-mv", "-Oz", "-o", staged],
            ],
            description="Variant calling",
        )
    ensure_vcf_indexed(out_vcf)
    _count(out_vcf, ref)


def _call_diploid_snps(
    base: BaseArgs,
    input_path: str,
    ref: str,
    contigs: list[str],
    out_vcf: str,
) -> None:
    """Autosome and chrX SNPs, called contig by contig as ``vcf snp --parallel``
    does, chrX with the sample's sex-aware ploidy."""
    from .vcf.basic import caller_setup, ploidy_args
    from .vcf.contig_calling import call_small_variants, calling_contigs

    region = ",".join(contigs)
    setup = caller_setup(
        input_path,
        threads=base.threads,
        outdir=base.outdir,
        ref=ref,
        region=region,
        qc_min_depth=None,
    )
    ploidy = ploidy_args(None, None, setup.lib)
    profile, prior = ultima.bcftools_options(input_path)
    logging.info(f"Calling autosome and chrX SNPs into {out_vcf}...")
    with (
        safe_output(out_vcf) as staged,
        sex_samples_args(input_path, base.outdir, ploidy, region=region) as sex_args,
    ):
        call_small_variants(
            input_path,
            ref,
            staged,
            threads=setup.threads,
            region=region,
            profile=profile,
            call_args=[*ploidy, *sex_args, *(["-P", "0"] if prior is None else prior)],
            contigs=calling_contigs(
                input_path, ref, setup.threads, region, parallel=True
            ),
        )
    ensure_vcf_indexed(out_vcf)
    _count(out_vcf, ref)


def _count(vcf: str, ref: str) -> None:
    current_result().add_metrics(variants=warn_if_no_variants(vcf, ref))


def _consensus(
    ref: str, contigs: list[str], vcf: str, out_fasta: str, diploid: bool
) -> None:
    """The reference's contigs with the calls applied: a haploid call's allele,
    or a diploid genotype as its IUPAC code."""
    logging.info(f"Writing the consensus FASTA {out_fasta}...")
    with safe_output(out_fasta) as staged, open(staged, "wb") as f:
        run_pipeline(
            [
                ["samtools", "faidx", ref, *contigs],
                ["bcftools", "consensus", "-f", "-", "-H"]
                + ["I" if diploid else "1", vcf],
            ],
            stdout=f,
            description="Consensus generation",
        )
