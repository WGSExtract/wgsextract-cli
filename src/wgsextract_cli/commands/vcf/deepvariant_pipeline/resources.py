"""DeepVariant host requirements and bounded postprocessing."""

import logging
import math
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from wgsextract_cli.core.alignment import memory as alignment_memory
from wgsextract_cli.core.alignment.cram_staging import (
    cram_version,
    indexed_slice_bytes,
    region_intervals,
    staging_bytes,
)
from wgsextract_cli.core.containers import (
    docker,
    docker_info,
)
from wgsextract_cli.core.genotype_qc import contigs_with_reads
from wgsextract_cli.core.reference_assets import read_fai_lengths
from wgsextract_cli.core.resource_estimates import (
    GENOME_BASES,
    GIB,
    HostRequirement,
    PostprocessPlan,
    PreflightEstimate,
    calling_hours,
    dv_calling_regions,
    plan_dv_postprocess,
)
from wgsextract_cli.core.samtools_commands import alignment_index
from wgsextract_cli.core.utils import WGSExtractError, run_command

from .models import (
    DEFAULT_ARM64_IMAGE,
    DEFAULT_IMAGE,
    DEFAULT_ULTIMA_GPU_IMAGE,
    DEFAULT_ULTIMA_IMAGE,
    _dv_env,
)


def _docker_memory(setup: str | None) -> int | None:
    """The Docker engine's memory (e.g. Docker Desktop's VM), when it reports it."""
    if setup is None:
        return None
    engine = docker_info(setup, "{{.MemTotal}}").strip()
    return int(engine) if engine.isdigit() and int(engine) > 0 else None


def _available_memory(engine: int | None) -> int | None:
    """Free host RAM, capped by the Docker ``engine``'s memory."""
    try:
        available = alignment_memory.available_memory()
    except OSError:
        return None
    return available if engine is None else min(available, engine)


def _selected_share(
    reads: str, ref: str, intervals: dict[str, list[tuple[int, int]]]
) -> float | None:
    """The share of ``reads``' data in ``intervals``: their CRAI slices, or
    all the BAM index's reads on each contig they touch (reads can crowd
    into a capture region). None when the index can't say."""
    try:
        if cram_version(reads) is not None:
            size = Path(reads).stat().st_size
            selected = indexed_slice_bytes(reads, ref, intervals)
            return None if selected is None or not size else min(1.0, selected / size)
        if not alignment_index(reads):
            return None
        from wgsextract_cli.commands.info.metrics import parse_idxstats

        stats, *_ = parse_idxstats(reads)
    except (OSError, ValueError, subprocess.SubprocessError, WGSExtractError):
        return None
    total = sum(stat["mapped"] + stat["unmapped"] for stat in stats)
    picked = sum(stat["mapped"] for stat in stats if stat["name"] in intervals)
    return picked / total if total else None


def _host_requirement(
    estimate: PreflightEstimate | None,
    floor: PreflightEstimate | None,
    *,
    input_file: str,
    ref: str,
    region: str | None,
    threads: int,
    model_type: str,
    native: bool,
    native_gpu: bool,
    staged_reads: bool,
    work: Path,
    engine: int | None,
) -> HostRequirement:
    """DeepVariant's host requirement (#690): the estimate's disk (for a
    --region, the share of the input's data it selects, else all of it)
    beside any CRAM 3.0 copy; its RAM, and the least it can run in
    (``floor``: one thread, a batch of 256); and its time for the bases it
    calls on contigs with reads."""
    lengths = read_fai_lengths(ref)
    try:
        regions = dv_calling_regions(lengths, region) or []
    except (OSError, ValueError) as error:
        logging.debug("DeepVariant's calling regions weren't sized: %s", error)
        regions = []
    intervals: dict[str, list[tuple[int, int]]] = {}
    for contig, start, end in regions:
        intervals.setdefault(contig, []).append((start, end))
    # Without resolved intervals (no .fai), keep the whole-input bound.
    selected = (
        _selected_share(input_file, ref, intervals) if region and intervals else None
    )
    share = 1.0 if selected is None else selected
    disk: list[tuple[str, int | None]] = []
    memory = None
    for row in estimate.resources if estimate else ():
        if row.resource == "memory":
            memory = row.estimated_bytes
        elif row.paths:
            needed = row.estimated_bytes
            disk.append(
                (row.paths[0], None if needed is None else math.ceil(needed * share))
            )
    if staged_reads:
        try:
            copy: int | None = staging_bytes(
                input_file, ref, intervals if region else None
            )
        except OSError:
            copy = None
        disk.append((str(work), copy))
    try:
        with_reads = contigs_with_reads(input_file, ref)
    except (OSError, ValueError, subprocess.SubprocessError, WGSExtractError):
        with_reads = {}
    called = sum(
        end - start
        for contig, start, end in regions
        if not with_reads or contig in with_reads
    )
    caller = (
        "deepvariant-ont"
        if model_type == "ONT_R104"
        else ("wgse-deepvariant-gpu" if native_gpu else "wgse-deepvariant")
        if native
        else "deepvariant"
    )
    hours = (
        None
        if model_type == "WES"
        else calling_hours(caller, called or int(GENOME_BASES), threads)
    )
    capacity = None
    if engine is not None:
        capacity = min(engine, alignment_memory.total_memory())
    least = next(
        (r.estimated_bytes for r in floor.resources if r.resource == "memory"), None
    ) if floor else None  # fmt: skip
    return HostRequirement(
        "DeepVariant", tuple(disk), memory, threads, hours,
        memory_floor=least, memory_capacity=capacity,
    )  # fmt: skip


def _supports_num_partitions(
    image: str | None, run_deepvariant: str | None, setup: str | None
) -> bool:
    """Whether postprocess_variants has --num_partitions (DeepVariant 1.9+).

    Pinned defaults and version tags decide without running anything;
    otherwise ask the image's or launcher's postprocess_variants.
    """
    if image in {
        DEFAULT_IMAGE,
        DEFAULT_ARM64_IMAGE,
        DEFAULT_ULTIMA_IMAGE,
        DEFAULT_ULTIMA_GPU_IMAGE,
    }:
        return True
    if image:
        tag = re.search(r":v?(\d+)\.(\d+)[^/:@]*(?:@|$)", image)
        if tag:
            return (int(tag.group(1)), int(tag.group(2))) >= (1, 9)
        probe = [
            docker(setup or ""), "run", "--rm", "--pull=never",
            "--entrypoint", "/opt/deepvariant/bin/postprocess_variants",
            image, "--helpfull",
        ]  # fmt: skip
    elif run_deepvariant:
        tool = Path(run_deepvariant).with_name("postprocess_variants")
        if not tool.is_file():
            return False
        probe = [str(tool), "--helpfull"]
    else:
        return False
    try:
        result = run_command(probe, capture_output=True, check=False, env=_dv_env())
    except OSError:
        return False
    return "num_partitions" in (result.stdout or "") + (result.stderr or "")


def _postprocess_plan(
    model_type: str,
    ref: str,
    region: str | None,
    threads: int,
    available: int | None,
    supports_partitions: Callable[[], bool],
) -> PostprocessPlan | None:
    """Bound postprocess_variants by RAM; raise when one worker cannot fit.

    ``supports_partitions`` is asked only when more partitions are wanted.
    """
    if model_type == "WES":
        return None  # run_deepvariant postprocesses WES sequentially.
    lengths = read_fai_lengths(ref)
    if not lengths:
        logging.warning(
            f"No {ref}.fai: DeepVariant's postprocess_variants RAM isn't bounded."
        )
        return None
    if region:
        region_intervals(
            region, lengths, "DeepVariant"
        )  # Report bad selections clearly.
    regions = dv_calling_regions(lengths, region)
    assert regions is not None
    plan = plan_dv_postprocess(regions, threads, available)
    upgrade = ""
    if plan.partitions > plan.cpus and not supports_partitions():
        upgrade = ", use DeepVariant 1.9+ (which can split this step further)"
        logging.warning(
            "This DeepVariant's postprocess_variants has no --num_partitions "
            "(DeepVariant 1.9+), so only its workers can be reduced to save memory."
        )
        plan = plan_dv_postprocess(regions, threads, available, partitions=False)
    if plan.fits is False and available is not None:
        raise WGSExtractError(
            "Not enough memory for DeepVariant's postprocess_variants step: one "
            f"worker sorting {plan.partitions} partitions needs ~"
            f"{plan.worker_bytes / GIB:.1f} GiB plus 1 GiB of headroom, but "
            f"{available / GIB:.1f} GiB is available. Free memory (close other "
            f"jobs), use a machine with more RAM{upgrade}, or call a smaller "
            "--region."
        )
    if plan.cpus < threads:
        logging.warning(
            f"Limiting DeepVariant postprocess_variants to {plan.cpus} of {threads} "
            f"workers over {plan.partitions} partitions (~"
            f"{plan.worker_bytes / GIB:.1f} GiB each) to fit "
            f"{(available or 0) / GIB:.1f} GiB of available memory."
        )
    elif plan.partitions > threads:
        logging.info(
            f"Splitting DeepVariant postprocess_variants into {plan.partitions} "
            f"partitions (~{plan.worker_bytes / GIB:.1f} GiB per worker) to fit "
            f"{(available or 0) / GIB:.1f} GiB of available memory."
        )
    return plan


def _postprocess_args(plan: PostprocessPlan | None, threads: int) -> list[str]:
    if plan is None:
        return []
    flags = []
    if plan.cpus != threads:
        flags += ["--postprocess_cpus", str(plan.cpus)]
    if plan.partitions > plan.cpus:
        # postprocess_variants' --num_partitions needs DeepVariant 1.9+.
        flags += [
            "--postprocess_variants_extra_args",
            f"num_partitions={plan.partitions}",
        ]
    return flags
