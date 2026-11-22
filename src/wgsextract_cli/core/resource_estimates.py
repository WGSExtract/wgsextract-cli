"""Read-only, GUI-reusable resource estimates; all sizes are bytes."""

import logging
import math
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ._resource_inputs import (
    BAM_BYTES_PER_READ as BAM_BYTES_PER_READ,
)
from ._resource_inputs import (
    CRAM_BYTES_PER_READ as CRAM_BYTES_PER_READ,
)
from ._resource_inputs import (
    DV_BYTES_PER_RECORD as DV_BYTES_PER_RECORD,
)
from ._resource_inputs import (
    DV_GVCF_PER_INPUT_BYTE as DV_GVCF_PER_INPUT_BYTE,
)
from ._resource_inputs import (
    DV_MAX_PARTITIONS as DV_MAX_PARTITIONS,
)
from ._resource_inputs import (
    DV_RECORDS_PER_BASE as DV_RECORDS_PER_BASE,
)
from ._resource_inputs import (
    DV_WORKER_BASE_BYTES as DV_WORKER_BASE_BYTES,
)
from ._resource_inputs import (
    FASTQ_BYTES_PER_READ as FASTQ_BYTES_PER_READ,
)
from ._resource_inputs import (
    GATK_GVCF_BYTES_PER_READ as GATK_GVCF_BYTES_PER_READ,
)
from ._resource_inputs import (
    GATK_WORKER_BYTES as GATK_WORKER_BYTES,
)
from ._resource_inputs import (
    GIB as GIB,
)
from ._resource_inputs import (
    GVCF_COPIES as GVCF_COPIES,
)
from ._resource_inputs import (
    LONG_READ_FASTQ_PER_CRAM_BYTE as LONG_READ_FASTQ_PER_CRAM_BYTE,
)
from ._resource_inputs import (
    LONG_READ_OUTPUT_PER_CRAM_BYTE as LONG_READ_OUTPUT_PER_CRAM_BYTE,
)
from ._resource_inputs import (
    LONG_READ_SPILL_PER_CRAM_BYTE as LONG_READ_SPILL_PER_CRAM_BYTE,
)
from ._resource_inputs import (
    NATIVE_DV_BASE_BYTES as NATIVE_DV_BASE_BYTES,
)
from ._resource_inputs import (
    NATIVE_DV_CONTIG_BYTES_PER_BASE as NATIVE_DV_CONTIG_BYTES_PER_BASE,
)
from ._resource_inputs import (
    NATIVE_DV_EXAMPLES_PER_INPUT_BYTE as NATIVE_DV_EXAMPLES_PER_INPUT_BYTE,
)
from ._resource_inputs import (
    NATIVE_DV_GVCF_BYTES_PER_BASE as NATIVE_DV_GVCF_BYTES_PER_BASE,
)
from ._resource_inputs import (
    NATIVE_DV_GVCF_TEMPORARY_PER_INPUT_BYTE as NATIVE_DV_GVCF_TEMPORARY_PER_INPUT_BYTE,
)
from ._resource_inputs import (
    NATIVE_DV_INFERENCE_BYTES_PER_EXAMPLE as NATIVE_DV_INFERENCE_BYTES_PER_EXAMPLE,
)
from ._resource_inputs import (
    NATIVE_DV_PHASE_MERGE_BYTES_PER_INPUT_BYTE as NATIVE_DV_PHASE_MERGE_BYTES_PER_INPUT_BYTE,
)
from ._resource_inputs import (
    NATIVE_DV_RECORD_BYTES_PER_BASE as NATIVE_DV_RECORD_BYTES_PER_BASE,
)
from ._resource_inputs import (
    NATIVE_DV_THREAD_BYTES as NATIVE_DV_THREAD_BYTES,
)
from ._resource_inputs import (
    SORT_BYTES_PER_READ as SORT_BYTES_PER_READ,
)
from ._resource_inputs import (
    LongReadDisk as LongReadDisk,
)
from ._resource_inputs import (
    Operation as Operation,
)
from ._resource_inputs import (
    PostprocessPlan as PostprocessPlan,
)
from ._resource_inputs import (
    PreflightEstimate as PreflightEstimate,
)
from ._resource_inputs import (
    ResourceEstimate as ResourceEstimate,
)
from ._resource_inputs import (
    _disk_space,
)
from ._resource_inputs import (
    bcftools_batches as bcftools_batches,
)
from ._resource_inputs import (
    dv_calling_regions as dv_calling_regions,
)
from ._resource_inputs import (
    dv_partition_bases as dv_partition_bases,
)
from ._resource_inputs import (
    dv_postprocess_worker_bytes as dv_postprocess_worker_bytes,
)
from ._resource_inputs import (
    long_read_cram_bytes as long_read_cram_bytes,
)
from ._resource_inputs import (
    long_read_disk as long_read_disk,
)
from ._resource_inputs import (
    plan_dv_postprocess as plan_dv_postprocess,
)
from ._resource_parabricks import (
    PB_GPU_NEEDS as PB_GPU_NEEDS,
)
from ._resource_parabricks import (
    PB_HOST_RECOMMENDED as PB_HOST_RECOMMENDED,
)
from ._resource_parabricks import (
    PB_HOST_RECOMMENDED_PER_GPU as PB_HOST_RECOMMENDED_PER_GPU,
)
from ._resource_parabricks import (
    PB_LOW_MEMORY_FLOOR as PB_LOW_MEMORY_FLOOR,
)
from ._resource_parabricks import (
    PB_MIN_GPU_TOTAL as PB_MIN_GPU_TOTAL,
)
from ._resource_parabricks import (
    GpuMemory as GpuMemory,
)
from ._resource_parabricks import (
    ParabricksNeed as ParabricksNeed,
)
from ._resource_parabricks import (
    ParabricksPlan as ParabricksPlan,
)
from ._resource_pipeline import estimate_resources as estimate_resources
from .alignment import memory as alignment_memory


def preflight_resources(
    estimate: Callable[[], PreflightEstimate],
) -> PreflightEstimate | None:
    """Best-effort CLI reporting; the GUI estimator remains strict. Returns
    the estimate it reported, if any."""
    try:
        reported = estimate()
        report_resources(reported)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        logging.warning("resource estimate unavailable: %s", exc)
        return None
    return reported


def report_resources(estimate: PreflightEstimate) -> None:
    """Log one compact row per resource/filesystem, warning on shortages."""
    logging.info("Preflight %s: estimated peak / available (GiB)", estimate.operation)
    for resource in estimate.resources:
        needed = (
            "unknown"
            if resource.estimated_bytes is None
            else f"{resource.estimated_bytes / GIB:.2f}"
        )
        available = (
            "unknown"
            if resource.available_bytes is None
            else f"{resource.available_bytes / GIB:.2f}"
        )
        paths = f" at {', '.join(resource.paths)}" if resource.paths else ""
        logging.log(
            logging.INFO if resource.status == "OK" else logging.WARNING,
            "Preflight %-6s %-7s %s / %s GiB%s",
            resource.resource,
            resource.status,
            needed,
            available,
            paths,
        )
    for note in estimate.notes:
        logging.info("Preflight: %s", note)


# Approximate run times (#690), in thread-hours: a run on N threads takes about
# this over N hours. No step scales perfectly with threads and CPUs differ, so
# they only say whether a run takes minutes, hours or days.
# Short-read alignment per read base: 5M HG002 2x150 pairs (1.5 Gb) on hs38, 8
# threads of a Ryzen 5 5600X (#674/#736): bwa 472.5 s, wgse-bwa --cuda 131.4 s,
# bwa-mem3 --compat=bwa-mem 97.7 s. bwa-mem2 is assumed as fast as bwa-mem3.
_TIMED_BASES = 1.5e9
ALIGN_THREAD_SECONDS_PER_BASE = {
    "bwa": 472.5 * 8 / _TIMED_BASES,
    "bwa-gpu": 131.4 * 8 / _TIMED_BASES,
    "bwa-mem3": 97.7 * 8 / _TIMED_BASES,
}
# Extraction and sorting around the aligner: the 35x realign on a 16 GB M1
# spent 23 of its 362 minutes extracting FASTQs.
REALIGN_TIME_OVERHEAD = 1.15
# minimap2 -Y realigned the 103.7 GB HG002 ONT CRAM in 21.4 h on 7 threads of
# an Apple M1 (#431).
LONG_READ_THREAD_SECONDS_PER_CRAM_BYTE = 21.4 * 3600 * 7 / 103.7e9
# Calling a whole genome (about 3.1 Gb), in thread-hours.
GENOME_BASES = 3.1e9
CALLER_THREAD_HOURS = {
    # Google's CPU image on 30x short reads: 15 h on 4 threads (projected from
    # chr1 and chr20).
    "deepvariant": 60.0,
    # Google's CPU image on 51x ONT R10.4.1: 11.3 h on 10 threads (#431); also
    # used for wgse-deepvariant's ONT model, which isn't timed on a genome.
    "deepvariant-ont": 113.0,
    # wgse-deepvariant on 30x short reads: 7 h on 4 threads, 3 h with --gpu
    # (projected).
    "wgse-deepvariant": 28.0,
    "wgse-deepvariant-gpu": 12.0,
    # Clair3 on 42x ONT: chr20 in 10.3 min on 4 CPUs, about 8 h for a genome
    # (projected); with --gpu the 51x genome took 2.5 h on 6 threads (#431).
    "clair3": 33.0,
    "clair3-gpu": 15.0,
}
# Clair3's peak RAM, 15.2 GiB on 6 threads with --gpu (#431). Its CPU mode
# isn't measured on a genome, so a computer short of it is warned, not refused.
CLAIR3_MEMORY = 16 * GIB


def alignment_hours(engine: str, gpu: bool, bases: float, threads: int) -> float:
    """Approximate hours for a short-read realignment of ``bases`` read bases."""
    if engine == "bwa":
        rate = ALIGN_THREAD_SECONDS_PER_BASE["bwa-gpu" if gpu else "bwa"]
    else:
        rate = ALIGN_THREAD_SECONDS_PER_BASE["bwa-mem3"]
    return bases * rate * REALIGN_TIME_OVERHEAD / max(1, threads) / 3600


def long_read_alignment_hours(path: str, input_bytes: int, threads: int) -> float:
    """Approximate hours for minimap2 to realign long reads of this size."""
    cram = long_read_cram_bytes(path, input_bytes)
    return cram * LONG_READ_THREAD_SECONDS_PER_CRAM_BYTE / max(1, threads) / 3600


def calling_hours(caller: str, bases: int, threads: int) -> float:
    """Approximate hours for ``caller`` (a CALLER_THREAD_HOURS key) to call
    ``bases`` reference bases."""
    return CALLER_THREAD_HOURS[caller] * bases / GENOME_BASES / max(1, threads)


def _size(value: int) -> str:
    """``~12 GiB``, ``~1.5 GiB`` or ``<0.1 GiB``, rounded up."""
    gib = value / GIB
    if gib < 0.1:
        return "<0.1 GiB"
    return f"~{math.ceil(gib) if gib >= 10 else math.ceil(gib * 10) / 10:g} GiB"


def _duration(hours: float) -> str:
    if hours < 1:
        return f"~{max(1, round(hours * 60))} min"
    return f"~{hours:.1f} h" if hours < 10 else f"~{hours:.0f} h"


@dataclass(frozen=True)
class Volume:
    """The disk a run needs on one filesystem (None when unknown), and its
    free space."""

    paths: tuple[str, ...]
    needed: int | None
    free: int | None

    @property
    def short(self) -> bool:
        return (
            self.needed is not None
            and self.free is not None
            and self.needed > self.free
        )


@dataclass(frozen=True)
class HostRequirement:
    """What a run needs of this computer (#690): disk in each directory
    (bytes, None when unknown), RAM at its settings (``memory``) and the
    least it can't run without (``memory_floor``, None when unknown), and
    approximate hours on its threads. ``memory_capacity`` replaces this
    computer's RAM, as a Docker VM's memory does."""

    operation: str
    disk: tuple[tuple[str, int | None], ...]
    memory: int | None
    threads: int
    hours: float | None
    memory_floor: int | None = None
    memory_capacity: int | None = None

    def volumes(self) -> list[Volume]:
        """The disk needs summed by filesystem."""
        grouped: dict[object, Volume] = {}
        for path, needed in self.disk:
            device, free = _disk_space(path)
            old = grouped.get(device)
            if old is None:
                grouped[device] = Volume((path,), needed, free)
                continue
            total = (
                None if old.needed is None or needed is None else old.needed + needed
            )
            paths = old.paths if path in old.paths else (*old.paths, path)
            grouped[device] = Volume(paths, total, old.free)
        return list(grouped.values())

    def line(self, volumes: Sequence[Volume]) -> str:
        """``this realign run needs ~X GiB disk on PATH, ~Y GiB RAM, ~Z h on N
        threads``."""
        disks = [
            f"{'? GiB' if v.needed is None else _size(v.needed)} "
            f"{'' if i else 'disk '}on {', '.join(v.paths)}"
            for i, v in enumerate(volumes)
        ]
        memory = (
            "RAM not estimated" if self.memory is None else f"{_size(self.memory)} RAM"
        )
        threads = f"{self.threads} thread{'' if self.threads == 1 else 's'}"
        time = (
            f"on {threads} (time not estimated)"
            if self.hours is None
            else f"{_duration(self.hours)} on {threads} (time approximate)"
        )
        return (
            f"this {self.operation} run needs {' and '.join(disks) or 'no disk'}, "
            f"{memory}, {time}"
        )

    def shortfalls(
        self, volumes: Sequence[Volume], capacity: int | None
    ) -> tuple[list[str], list[str]]:
        """What this computer lacks: what refuses the run (too little free
        disk, or less RAM than the run's floor), and what is only warned
        about (less RAM than these settings need)."""
        refusals = [
            f"{_size(v.needed or 0)} disk on {', '.join(v.paths)}, "
            f"but {(v.free or 0) / GIB:.1f} GiB is free"
            for v in volumes
            if v.short
        ]
        cautions = []
        has = f"this computer has {(capacity or 0) / GIB:.1f} GiB"
        if self.memory_floor is not None and capacity and self.memory_floor > capacity:
            refusals.append(f"at least {_size(self.memory_floor)} RAM, but {has}")
        elif self.memory is not None and capacity and self.memory > capacity:
            cautions.append(f"{_size(self.memory)} RAM at these settings, but {has}")
        return refusals, cautions


def check_host_requirement(
    requirement: HostRequirement,
    *,
    enforce: bool,
    unenforced: str = "--no-preflight",
    disk_advice: str = "",
    memory_advice: str = "",
) -> None:
    """Print the one-line host requirement, record it on the result
    (``details.host_requirement``) and refuse a run this computer can't meet:
    too little free disk, or less RAM than the run can't do without. Less
    RAM than its settings need is warned about. With ``enforce`` False
    refusals are warnings too, naming why (``unenforced``). The advice says
    how to make the run fit the disk or the RAM."""
    from .results import current_result, warn
    from .utils import WGSExtractError

    volumes = requirement.volumes()
    capacity = requirement.memory_capacity
    if capacity is None:
        try:
            capacity = alignment_memory.total_memory()
        except OSError:
            capacity = None
    refusals, cautions = requirement.shortfalls(volumes, capacity)
    line = requirement.line(volumes)
    logging.log(
        logging.WARNING if refusals or cautions else logging.INFO,
        "Host requirement: %s.",
        line,
    )
    result = current_result()
    hours = requirement.hours
    result.set_details(
        {
            **(result.details or {}),
            "host_requirement": {
                "operation": requirement.operation,
                "line": line,
                "disk": [
                    {
                        "paths": list(v.paths),
                        "needed_bytes": v.needed,
                        "free_bytes": v.free,
                    }
                    for v in volumes
                ],
                "memory_bytes": requirement.memory,
                "memory_floor_bytes": requirement.memory_floor,
                "memory_capacity_bytes": capacity,
                "threads": requirement.threads,
                "approximate_hours": None if hours is None else float(f"{hours:.2g}"),
                "shortfalls": refusals + cautions,
                "enforced": enforce,
            },
        }
    )
    disk_short = any(v.short for v in volumes)
    memory_short = bool(cautions) or len(refusals) > sum(v.short for v in volumes)
    details: dict[str, str | int | float | bool | None] = {
        "operation": requirement.operation,
        "disk_short": disk_short,
        "memory_short": memory_short,
    }
    fit = "".join(
        f" {advice}"
        for advice, short in ((disk_advice, disk_short), (memory_advice, memory_short))
        if advice and short
    )
    if cautions and not refusals:
        warn(
            "host_requirement_memory",
            f"This {requirement.operation} run needs {cautions[0]}; it may swap "
            f"or be killed.{fit}",
            operation=requirement.operation,
        )
    if not refusals:
        return
    message = (
        f"This computer can't meet this {requirement.operation} run's "
        f"requirement: it needs {'; '.join(refusals + cautions)}."
    )
    if not enforce:
        warn("host_requirement", f"{message} Running anyway ({unenforced}).", **details)
        return
    result.warn("host_requirement", message, **details)
    raise WGSExtractError(
        f"{message}{fit} Rerun with --no-preflight to override (not recommended)."
    )


# vep-rs's GRCh38 cache, built by `vep download --engine vep-rs` (#602, measured
# on Ensembl release 116): Ensembl's files, the unpacked genome FASTA the
# builder reads, the cache itself, and vep-cache-builder's peak memory.
VEP_RS_DOWNLOAD_BYTES = {False: int(1.6 * GIB), True: int(17.5 * GIB)}
VEP_RS_GENOME_BYTES = int(3.0 * GIB)
VEP_RS_CACHE_BYTES = {False: int(5.7 * GIB), True: 17 * GIB}
# vep-cache-builder peaked at 8.5 GB RSS without variations, 11 GB with.
VEP_RS_BUILD_MEMORY = 12 * GIB
# Its T2T-CHM13v2.0 cache (#630), from Ensembl's rapid-release gene set: 0.95
# GB of downloads, a 3.0 GB genome, a 1.2 GB cache, 2.8 GB peak RSS; no
# variations.
VEP_RS_CHM13_BYTES = {
    "downloads": int(1.0 * GIB),
    "genome": int(3.0 * GIB),
    "cache": int(1.3 * GIB),
}
VEP_RS_CHM13_BUILD_MEMORY = 4 * GIB
# Peak memory of `vep run` on a whole genome: vep-rs on its built cache (6
# threads, HG002), Ensembl VEP (6 forks).
VEP_RUN_MEMORY = {"vep-rs": 8 * GIB, "ensembl": int(1.5 * GIB)}
# Clair3 2.0.5's work folder at its peak on the GPU, in bytes per called
# reference base by mean depth (#811; HG002 ONT R10.4.1, chr20:10-20 Mb
# subsampled from 43x). The GPU mode writes every full-alignment tensor (23.5 kB
# per candidate) before predicting any, and shallower reads send it more
# low-quality candidates. The whole genome (43x) needed 1.1x the slice's rate
# and all of chr20 1.44x, hence the margin; the CPU mode pipes the tensors
# (chr20: 0.9 bytes per base).
CLAIR3_GPU_BYTES_PER_BASE = (
    (6.3, 122.6), (10.7, 104.7), (15.1, 64.6), (21.6, 41.5), (32.6, 33.2),
    (43.4, 31.4),
)  # fmt: skip
CLAIR3_GPU_MARGIN = 1.45
CLAIR3_ASSUMED_DEPTH = 30.0
CLAIR3_CPU_BYTES_PER_BASE = 2.0
# --reference-evidence gvcf (#863), HG002 ONT R10.4.1 chr20:10-15 Mb on 4
# threads and an RTX 3090: the gVCF pileup took the CPU from 75 s to 179 s
# (2.4x) and --gpu from 106 s to 452 s (4.3x), so with the gVCF the CPU was
# faster. Its gVCF was 1.6 MB (0.32 bytes per base). The partitions' parts,
# their gathered copy and the published gVCF can coexist: three copies.
CLAIR3_GVCF_TIME_FACTOR = {"clair3": 2.4, "clair3-gpu": 4.3}
CLAIR3_GVCF_BYTES_PER_BASE = 0.35


def estimate_vep_download(
    cache_dir: str, variations: bool, assembly: str = "GRCh38"
) -> PreflightEstimate:
    """Disk and memory a vep-rs cache build needs: the downloads and the
    unpacked genome stay until the cache beside them is complete."""
    from wgsextract_cli.commands._vep_engines import CHM13

    _, free = _disk_space(cache_dir)
    if assembly == CHM13:
        components, memory = dict(VEP_RS_CHM13_BYTES), VEP_RS_CHM13_BUILD_MEMORY
    else:
        components = {
            "downloads": VEP_RS_DOWNLOAD_BYTES[variations],
            "genome": VEP_RS_GENOME_BYTES,
            "cache": VEP_RS_CACHE_BYTES[variations],
        }
        memory = VEP_RS_BUILD_MEMORY
    return PreflightEstimate(
        "vep-download",
        (
            ResourceEstimate(
                "disk", sum(components.values()), free, components, (cache_dir,)
            ),
            ResourceEstimate("memory", memory, _available_memory(), {}),
        ),
        (),
    )


def estimate_vep_run(engine: str) -> PreflightEstimate:
    """Memory `vep run` needs for a whole genome with ``engine``."""
    return PreflightEstimate(
        "vep",
        (ResourceEstimate("memory", VEP_RUN_MEMORY[engine], _available_memory(), {}),),
        (),
    )


def _clair3_gpu_bytes_per_base(depth: float) -> float:
    """Interpolated in depth; held flat beyond the measured depths."""
    points = CLAIR3_GPU_BYTES_PER_BASE
    if depth <= points[0][0]:
        return points[0][1]
    for (d0, b0), (d1, b1) in zip(points, points[1:], strict=False):
        if depth <= d1:
            return b0 + (b1 - b0) * (depth - d0) / (d1 - d0)
    return points[-1][1]


def estimate_clair3(
    output_dir: str,
    bases: int,
    *,
    gpu: bool,
    depth: float | None = None,
    gvcf: bool = False,
) -> PreflightEstimate:
    """Peak of Clair3's work folder beside the output for ``bases`` called
    reference bases at ``depth`` (#811), and with ``gvcf`` the copies of its
    gVCF (#863)."""
    _, free = _disk_space(output_dir)
    notes = []
    if gpu:
        if depth is None:
            depth = CLAIR3_ASSUMED_DEPTH
            notes.append(f"Read depth unknown; Clair3's tensors sized for {depth:g}x.")
        rate = _clair3_gpu_bytes_per_base(depth) * CLAIR3_GPU_MARGIN
        notes.append(
            f"Clair3 on the GPU stores every full-alignment tensor first: "
            f"{bases / 1e6:,.0f} Mb at {depth:.0f}x."
        )
    else:
        rate = CLAIR3_CPU_BYTES_PER_BASE
    work = math.ceil(bases * rate)
    components = {"work": work}
    if gvcf:
        components["reference_evidence"] = math.ceil(
            bases * CLAIR3_GVCF_BYTES_PER_BASE * GVCF_COPIES
        )
        notes.append(
            f"Clair3's gVCF pileup takes about {CLAIR3_GVCF_TIME_FACTOR['clair3-gpu' if gpu else 'clair3']:g}x "
            "the calling time."
        )
    total = sum(components.values())
    return PreflightEstimate(
        "clair3",
        (ResourceEstimate("disk", total, free, components, (output_dir,)),),
        tuple(notes),
    )


def estimate_imputation(*, reads: bool, threads: int, bases: int) -> PreflightEstimate:
    """HG002 chr20 measurements (#621), scaled by selected reference bases.

    Sequential chunks keep memory roughly constant; thread scaling of time
    and extrapolation beyond chr20 are estimates, not measured genome runs.
    """
    fraction = bases / 64_444_167
    seconds = (450 if reads else 60) * fraction * 4 / max(1, threads)
    tool = "GLIMPSE2" if reads else "Beagle"
    memory = 4 * GIB if reads else 8 * GIB
    return PreflightEstimate(
        "impute",
        (ResourceEstimate("memory", memory, _available_memory(), {}),),
        (
            f"{tool}: estimated {seconds / 3600:.2f} h at {threads} threads "
            f"for {bases:,} reference bases (HG002 chr20 extrapolation).",
            "Excludes one-time panel preparation (~10 min for chr20); "
            "GLIMPSE2 whole autosomes ~5.5 h at 4 threads, ~2.8 h at 8. "
            "Actual runtime depends on depth, hardware and panel.",
        ),
    )


def _available_memory() -> int | None:
    try:
        return alignment_memory.available_memory()
    except (OSError, ValueError):
        return None
