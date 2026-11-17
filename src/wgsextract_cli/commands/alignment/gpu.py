"""Alignment gpu helpers."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from wgsextract_cli.core.alignment.fastq import (
    check_primary_reads,
)
from wgsextract_cli.core.alignment.profiles import (
    output_profile_options,
)
from wgsextract_cli.core.atomic_output import (
    atomic_outputs_enabled,
    safe_output,
)
from wgsextract_cli.core.genome_library import pair_stem
from wgsextract_cli.core.messages import LOG_MESSAGES
from wgsextract_cli.core.temp_directory import configured_temp_directory
from wgsextract_cli.core.utils import (
    UsageError,
    run_command,
)

from .._extract_helpers import cleanup_files
from .output import (
    _ensure_read_group_sample,
    _index_output,
    record_alignment,
)
from .read_groups import (
    _RG_PLATFORMS,
    read_group,
)
from .selection import (
    _minimap2_preset,
)

if TYPE_CHECKING:
    pass

from .common import (
    AlignJob,
    _AlignSetup,
    ensure_bwa_alt_file,
    ensure_bwa_index,
)
from .preparation import (
    _prepare_alignment,
    _validated_read_setup,
)

# Parabricks minimap2 presets for this command's long-read presets.
_GPU_MINIMAP2_PRESETS = {"map-hifi": "map-hifi", "map-ont": "map-ont"}


def _run_gpu(job: AlignJob, aligner: str) -> None:
    """The same alignment on an NVIDIA GPU: Parabricks fq2bam for BWA, its
    minimap2 for HiFi and ONT reads (map-pbmm2 standing in for pbmm2)."""
    if aligner == "bwa":
        _align_gpu_bwa(job)
        return
    if aligner == "pbmm2":
        if (job.preset or ("subread" if job.platform == "clr" else "ccs")) not in (
            "ccs",
            "map-hifi",
        ):
            raise UsageError("--gpu aligns PacBio HiFi/CCS reads, not CLR/subreads.")
        _align_gpu_minimap2(job, "map-pbmm2")
        return
    preset = _minimap2_preset(job)
    if preset not in _GPU_MINIMAP2_PRESETS:
        raise UsageError(
            f"--gpu has no minimap2 preset for {preset}: it aligns HiFi (map-hifi) "
            "and ONT (map-ont) long reads, and short reads with --aligner bwa."
        )
    _align_gpu_minimap2(job, _GPU_MINIMAP2_PRESETS[preset])


def _align_gpu_bwa(job: AlignJob) -> None:
    """BWA-MEM, sorting and duplicate marking on the GPU (pbrun fq2bam), with
    the same index, ALT contigs and read group as align_bwa."""
    from wgsextract_cli.core.parabricks import host_path, verify_parabricks

    setup = _prepare_alignment(
        job, ["samtools"], "GPU alignment", samtools_sort=False, validate_reads=False
    )
    verify_parabricks()
    fit = _gpu_memory_options(job, "fq2bam")
    setup = _validated_read_setup(job, setup)
    ensure_bwa_index(setup.ref)
    ensure_bwa_alt_file(setup.ref)
    # fq2bam requires a PU (platform unit) in the read group.
    rg = read_group(setup.stem, job.sample, job.platform) + f"\\tPU:{setup.stem}"
    reads = [host_path(setup.r1)] + ([host_path(job.r2)] if job.r2 else [])
    fq_arg = "--in-fq" if job.r2 else "--in-se-fq"
    _gpu_align(job, setup, "fq2bam", [fq_arg, *reads, rg, *fit], reads)


def _align_gpu_minimap2(job: AlignJob, preset: str) -> None:
    """Long reads (FASTQ or unaligned BAM) with Parabricks minimap2."""
    from wgsextract_cli.core.parabricks import host_path, verify_parabricks

    if job.r2:
        raise UsageError("Long-read alignment takes single-end input; drop --r2.")
    setup = _prepare_alignment(
        job,
        ["samtools"],
        "GPU long-read alignment",
        samtools_sort=False,
        validate_reads=False,
    )
    verify_parabricks()
    fit = _gpu_memory_options(job, "minimap2")
    setup = _validated_read_setup(job, setup)
    reads = host_path(setup.r1)
    in_arg = "--in-bam" if reads.lower().endswith(".bam") else "--in-fq"
    sample = job.sample or pair_stem(setup.stem)
    platform = _RG_PLATFORMS.get(job.platform or "", "PACBIO")
    _gpu_align(
        job,
        setup,
        "minimap2",
        [in_arg, reads, "--preset", preset]
        + ["--read-group-sm", sample, "--read-group-lb", sample]
        + ["--read-group-pl", platform, "--read-group-id-prefix", setup.stem]
        + fit,
        [reads],
    )
    # From an unaligned BAM, Parabricks keeps the BAM's own read groups, which
    # PacBio writes without the sample name callers need.
    _ensure_read_group_sample(_gpu_output_path(job, setup), sample, setup.ref)


def _gpu_memory_options(job: AlignJob, tool: str) -> list[str]:
    """Check the GPUs' and the host's memory for Parabricks ``tool``, and the
    options that fit it to them."""
    from wgsextract_cli.core.parabricks import plan_gpu_memory

    return plan_gpu_memory(tool, "align", check=not job.no_gpu_memory_check)


def _gpu_output_path(job: AlignJob, setup: _AlignSetup) -> str:
    from wgsextract_cli.core.parabricks import host_path

    ext = ".cram" if job.format == "CRAM" else ".bam"
    return host_path(str(Path(setup.outdir, f"{setup.stem}_aligned{ext}")))


def _gpu_align(
    job: AlignJob,
    setup: _AlignSetup,
    tool: str,
    tool_args: list[str],
    reads: list[str],
) -> None:
    """Run a Parabricks aligner into <stem>_aligned.bam/.cram. Parabricks can't
    write CRAM against a gzipped reference (it crashes), so then it writes BAM
    and samtools converts it."""
    from wgsextract_cli.core.parabricks import host_path, run_pbrun

    out_path = _gpu_output_path(job, setup)
    convert = out_path.endswith(".cram") and (
        setup.ref.lower().endswith(".gz") or job.output_profile not in (None, "default")
    )
    pb_out = out_path.removesuffix(".cram") + ".bam" if convert else out_path
    logging.info(
        LOG_MESSAGES["aligning_reads"].format(
            input=job.r1, output=out_path, tool=f"Parabricks {tool}"
        )
    )
    with (
        cleanup_files(
            on_failure=[out_path, pb_out, pb_out + ".bai"],
            preserve_outputs=atomic_outputs_enabled(),
        ),
        tempfile.TemporaryDirectory(
            dir=configured_temp_directory() or setup.outdir, prefix=".wgse_pb_"
        ) as tmp,
    ):
        with safe_output(out_path) as staged:
            if atomic_outputs_enabled():
                pb_out = str(Path(tmp, "aligned.bam")) if convert else staged
            run_pbrun(
                tool,
                ["--ref", host_path(setup.ref), *tool_args]
                + ["--out-bam", pb_out, "--tmp-dir", host_path(tmp)],
                [setup.ref, *reads, pb_out, tmp],
            )
            if convert:
                run_command(
                    [
                        "samtools",
                        "view",
                        "-C",
                        "-@",
                        setup.compression_threads or setup.threads,
                        "-T",
                        setup.ref,
                    ]
                    + output_profile_options(job.output_profile or "default")
                    + ["-o", staged, pb_out]
                )
            if job.exact_reads is not None:
                check_primary_reads(staged, job.exact_reads, setup.ref)
            Path(pb_out.rsplit(".", 1)[0] + "_chrs.txt").unlink(missing_ok=True)
        if convert or atomic_outputs_enabled():
            _index_output(out_path, threads=setup.threads)
        if convert:
            for path in (pb_out, pb_out + ".bai"):
                Path(path).unlink(missing_ok=True)
    # Parabricks also lists the output's chromosomes beside it.
    Path(pb_out.rsplit(".", 1)[0] + "_chrs.txt").unlink(missing_ok=True)
    record_alignment(out_path, f"parabricks {tool}", setup.ref)
