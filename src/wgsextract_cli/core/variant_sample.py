"""Prepare single-sample working copies without a full-cohort intermediate."""

import logging
import re
import subprocess
from contextlib import ExitStack
from os import fspath
from pathlib import Path

from .paths import PathInput
from .temp_directory import scratch_directory
from .utils import WGSExtractError, popen, run_command, run_pipeline
from .variant_files import ensure_vcf_indexed, vcf_header_chromosomes


def needs_sample_working_copy(vcf: str) -> bool:
    """Whether microarray needs a single-sample copy before region lookup."""
    suffix = Path(vcf).suffix
    if suffix != ".gz":
        return suffix == ".vcf"
    try:
        # HTSlib supplies missing contig declarations from a readable VCF index.
        ensure_vcf_indexed(vcf)
    except WGSExtractError as exc:
        if not isinstance(exc.__cause__, subprocess.CalledProcessError):
            raise
        logging.debug("Using a working sample copy: %s", exc)
        return True
    return False


def indexed_sample_vcf(vcf: str, sample: str, outdir: str, cleanup: ExitStack) -> str:
    """One selected sample, in an indexed scratch VCF that cleanup removes."""
    Path(outdir).mkdir(parents=True, exist_ok=True)
    workdir = Path(cleanup.enter_context(scratch_directory(outdir, "Sample_")))
    subset = workdir / "sample.vcf.gz"
    try:
        subset_vcf_sample(vcf, sample, subset)
        index = ["bcftools", "index", "-f"]
        if run_command([*index, subset], capture_output=True, check=False).returncode:
            ordered = workdir / "sorted.vcf.gz"
            run_command(
                ["bcftools", "sort", "-m", "128M", "-T", workdir / "sort"]
                + ["-Oz", "-o", ordered, subset]
            )
            subset.unlink()
            subset = ordered
            run_command([*index, subset])
    except (OSError, subprocess.SubprocessError, WGSExtractError) as exc:
        raise WGSExtractError(f"Could not select sample '{sample}': {exc}") from exc
    return str(subset)


def subset_vcf_sample(vcf: PathInput, sample: str, out: Path) -> None:
    """Write one sample as BGZF, repairing undeclared VCF contigs on demand."""
    view = ["bcftools", "view", "--samples", sample, "--trim-alt-alleles"]
    output = ["-Oz", "-o", out]
    try:
        run_command([*view, *output, vcf], capture_output=True)
    except subprocess.CalledProcessError as exc:
        if not re.search(r"Contig .+ is not defined in the header", exc.stderr or ""):
            raise
        header = _header_with_record_contigs(vcf)
        if header is None:
            raise
        logging.info("Adding missing VCF contig declarations for sample selection.")
        new_header = out.with_name("sample-header.txt")
        new_header.write_text(header, encoding="utf-8", newline="\n")
        run_pipeline(
            [["bcftools", "reheader", "-h", new_header, vcf], [*view, *output, "-"]],
            description="Select VCF sample with repaired header",
            stderr_dir=out.parent,
        )


def _header_with_record_contigs(vcf: PathInput) -> str | None:
    """The replacement header, or None if all record contigs are declared."""
    header = run_command(
        ["bcftools", "view", "--no-version", "-h", vcf], capture_output=True
    ).stdout
    declared = set(vcf_header_chromosomes(header))
    missing: dict[str, None] = {}
    command = ["bcftools", "query", "-f", "%CHROM\\n", fspath(vcf)]
    with popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as query:
        assert query.stdout is not None
        for line in query.stdout:
            contig = line.rstrip(b"\r\n").decode()
            if contig not in declared:
                missing.setdefault(contig, None)
        if query.wait():
            raise subprocess.CalledProcessError(query.returncode, command)
    if not missing:
        return None
    meta, columns = header.rstrip("\n").rsplit("\n", 1)
    return "\n".join([meta, *(f"##contig=<ID={c}>" for c in missing), columns, ""])
