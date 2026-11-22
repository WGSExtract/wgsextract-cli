"""Shared inputs and result types for resource estimates."""

import math
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import conversion_resources as _conversion
from .region_selection import selected_intervals

Operation = Literal[
    "align",
    "realign",
    "sort",
    "to-bam",
    "to-cram",
    "snp",
    "indel",
    "gatk",
    "deepvariant",
    "clair3",
    "vep-download",
    "vep",
    "impute",
]
GIB = 1024**3
GATK_WORKER_BYTES = 6 * GIB
# Conservative 30x envelope: 680M reads -> 81.6 GB sort spill, 19.0 GB CRAM.
# H2's binned-quality CRAM was only 8.63 GB; don't assume all inputs compress so well.
SORT_BYTES_PER_READ = 120
CRAM_BYTES_PER_READ = _conversion.CRAM_BYTES_PER_READ
BAM_BYTES_PER_READ = _conversion.BAM_BYTES_PER_READ
FASTQ_BYTES_PER_READ = 100
# HG002 30x, #722: 2.95 MB SNP and 0.80 MB indel VCF.gz from a 670.5 MB BAM.
# Leave several times the measured size, normalizing BAM/CRAM via disk_reads.
_BCFTOOLS_VCF_BYTES_PER_READ = {"snp": 1.6, "indel": 0.4}
# --reference-evidence gvcf, HG002 35x chr20 (#863): 7.8 MB from 19.5M reads.
_BCFTOOLS_GVCF_BYTES_PER_READ = 2.0
_BCFTOOLS_PART_HEADER_BYTES = 16 * 1024
_BCFTOOLS_JOBS_PER_WORKER = 16
# Long reads realigned with minimap2 -Y, which keeps every supplementary
# record's full SEQ, QUAL and modification tags, per byte of input CRAM (#474).
# HG002 ONT (PAO83395.pass.cram) onto hs38: the whole genome's sort spills
# reached 2.8x by 12% of the input. A chr20 slice whose supplementary
# alignments add 1.93x its bases, against 1.42-1.70x elsewhere, came to 3.35x
# as spills and 1.9x as CRAM; scaled to a 1.55x genome, 2.7x and 1.5x. The
# whole genome's CRAM came to 1.79x (#431), 5% above the 1.7x first budgeted
# from the slice; BAM's slice-scaled 2.6x is raised by the same 5% (#690).
LONG_READ_SPILL_PER_CRAM_BYTE = 3.0
LONG_READ_OUTPUT_PER_CRAM_BYTE = {"CRAM": 1.8, "BAM": 2.75}
LONG_READ_FASTQ_PER_CRAM_BYTE = 1.45
# The same reads as level-6 BAM, bgzipped and plain tagged FASTQ.
_LONG_READ_BYTES_PER_CRAM_BYTE = {"bam": 1.76, "gz": 1.39, "plain": 3.3}
# samtools sort spills its whole -m x threads buffer into each file, at about
# 0.65 of the records' decoded size, then merges every 32 files into a new one
# beside them.
_SORT_SPILL_PER_BUFFER_BYTE = 0.65
_SORT_CONSOLIDATED_FILES = 33
# DeepVariant postprocess_variants: each partition worker loads and sorts its
# partition's call_variants records in memory. #173 (HG008 Ultima ~30x, hs38DH,
# --cpus 5): DeepVariant's five partitions held 1.06-3.30 million records; the
# densest, chr13-chr20 (700,430,089 bases), held 3,295,319 (4.70 per kb). Three
# workers were OOM-killed mid-sort at 3.39-6.14 GB anonymous RSS, below their peaks.
DV_RECORDS_PER_BASE = 4.75e-3
DV_BYTES_PER_RECORD = 2048
DV_WORKER_BASE_BYTES = 768 * 1024**2
DV_MAX_PARTITIONS = 128
# wgse-deepvariant: #647 measured 12.9 GB inference footprint at batch 1024.
# #721, 0.1.1 on a 16 GiB M1, six threads, HG002 35x chr20: 6.56 GB
# inference footprint at batch 512; 0.84 GB workspace from a 1.09 GB BAM.
# The 0.1.0 whole-genome run peaked at 1.62/2.24/3.70 GB stage RSS
# (make/infer/postprocess); batch-512 Metal inference footprint was 5.26 GB,
# with 17.3 GB workspace from a 46.0 GB BAM. The batch-512 RAM envelope is
# 7 GiB for inference (1 GiB base + 512 * 12 MiB) + 1 GiB driver headroom.
# The allowances below remain conservative; 0.1.1 WGS is not yet measured.
# postprocess_variants keeps the run's records serialized (~0.6 B/base) and
# parses one contig at a time (~10 B/base). The 0.8 temporary bytes/input byte
# allowance is shared by regional and whole-genome runs; chr20 needs a larger
# ratio than the measured whole genome.
# On Linux (#719) call_variants peaked at 1.5 GiB at batch 256,
# on CUDA or the CPU: within the Mac's estimate.
NATIVE_DV_BASE_BYTES = GIB
NATIVE_DV_THREAD_BYTES = GIB // 4
NATIVE_DV_INFERENCE_BYTES_PER_EXAMPLE = 12 * 1024**2
NATIVE_DV_RECORD_BYTES_PER_BASE = 0.6
NATIVE_DV_CONTIG_BYTES_PER_BASE = 10.0
NATIVE_DV_EXAMPLES_PER_INPUT_BYTE = 0.8
# #739: for ONT_R104, postprocess_variants first merges every read's phase in
# each region (282,000 records for HG002 ONT chr20); 48 times that, a whole
# genome's worth, took 4.2 GiB, about 0.055 bytes per byte of its 81 GB CRAM.
NATIVE_DV_PHASE_MERGE_BYTES_PER_INPUT_BYTE = 0.06
# --reference-evidence gvcf (#863): wgse-deepvariant's postprocess_variants
# keeps every contig's calls until it writes the gVCF (patch 0005). HG002 35x
# Illumina chr20+chr22 peaked at 0.94 GiB with the gVCF and 0.81 without;
# adding chr22 to chr20 added 3.5 B/base beyond the serialized records. Allow
# 5 (the 4.75 per kb density above, against 3.3 measured) for every called
# base. Its make_examples' gVCF records took 0.26 more temporary bytes per
# input byte (285 MiB from a 1.09 GB BAM), the published gVCF 1.9% of the
# input (20.8 MB for chr20; ~1 GB for a 30x genome).
NATIVE_DV_GVCF_BYTES_PER_BASE = 5.0
NATIVE_DV_GVCF_TEMPORARY_PER_INPUT_BYTE = 0.4
DV_GVCF_PER_INPUT_BYTE = 0.05
# GATK's gVCF with GQ bands 10,20,30,60 (#863): 2.6% of HG002 35x chr20's
# BAM; 3% of its bytes at the 80 BAM bytes per read disk_reads infers.
GATK_GVCF_BYTES_PER_READ = 0.03 * 80


@dataclass(frozen=True)
class ResourceEstimate:
    resource: str
    estimated_bytes: int | None
    available_bytes: int | None
    components: dict[str, int]
    paths: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.estimated_bytes is None or self.available_bytes is None:
            return "UNKNOWN"
        return "OK" if self.estimated_bytes <= self.available_bytes else "LOW"

    def to_dict(self) -> dict[str, object]:
        return {
            "resource": self.resource,
            "estimated_bytes": self.estimated_bytes,
            "available_bytes": self.available_bytes,
            "status": self.status,
            "components": dict(self.components),
            "paths": list(self.paths),
        }


@dataclass(frozen=True)
class PreflightEstimate:
    operation: Operation
    resources: tuple[ResourceEstimate, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Versioned JSON-compatible data, without executing any external tool."""
        return {
            "schema_version": 1,
            "operation": self.operation,
            "resources": [resource.to_dict() for resource in self.resources],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class PostprocessPlan:
    """DeepVariant postprocess_variants workers (--cpus) and --num_partitions."""

    cpus: int
    partitions: int
    worker_bytes: int
    fits: bool | None

    @property
    def peak_bytes(self) -> int:
        return self.cpus * self.worker_bytes


def dv_calling_regions(
    lengths: dict[str, int], region: str | None
) -> list[tuple[str, int, int]] | None:
    """DeepVariant's calling regions in reference order; None without lengths."""
    if not lengths:
        return None
    if not region:
        return [(contig, 0, length) for contig, length in lengths.items()]
    selected = selected_intervals(region, lengths)
    return [(c, start, end) for c in lengths for start, end in selected.get(c, ())]


def dv_partition_bases(
    regions: Sequence[tuple[str, int, int]], partitions: int
) -> list[int] | None:
    """Bases per partition from DeepVariant 1.9+'s partition_calling_regions.

    None when DeepVariant itself would fail: fewer bases than partitions, or a
    single oversized piece left to split.
    """
    total = sum(end - start for _, start, end in regions)
    size = total // partitions
    if size < 1:
        return None
    pieces = [
        min(end, at + size) - at
        for _, start, end in regions
        for at in range(start, end, size)
    ]
    prefix = [0]
    for piece in pieces:
        prefix.append(prefix[-1] + piece)

    def bases(group: tuple[int, int]) -> int:
        return prefix[group[1]] - prefix[group[0]]

    # Groups are contiguous [first, last) piece ranges, closed once they exceed size.
    groups: list[tuple[int, int]] = []
    first = 0
    for index in range(len(pieces)):
        if index > first and prefix[index] - prefix[first] > size:
            groups.append((first, index))
            first = index
    groups.append((first, len(pieces)))
    while len(groups) < partitions:
        groups.sort(key=bases)
        first, last = groups.pop()
        middle = first + (last - first) // 2
        if middle == first:
            return None
        groups += [(first, middle), (middle, last)]
    return [bases(group) for group in sorted(groups)]


def dv_postprocess_worker_bytes(bases: int) -> int:
    records = math.ceil(bases * DV_RECORDS_PER_BASE)
    return DV_WORKER_BASE_BYTES + records * DV_BYTES_PER_RECORD


def plan_dv_postprocess(
    regions: Sequence[tuple[str, int, int]],
    threads: int,
    available: int | None,
    partitions: bool = True,
) -> PostprocessPlan:
    """Bound postprocess_variants' in-memory partition sorts by available RAM.

    DeepVariant's default is ``threads`` workers over as many partitions. If
    that does not fit beside 1 GiB of driver headroom, keep the most workers
    and add the fewest partitions that make their largest partitions fit.
    Without known RAM the default is kept (``fits`` None); when one worker
    cannot fit, the plan is the smallest one-worker plan with ``fits`` False.
    Without ``partitions`` (DeepVariant before 1.9 has no --num_partitions),
    only the workers, and so the partitions with them, are reduced.
    """
    workers: dict[int, int | None] = {}

    def worker(partitions: int) -> int | None:
        if partitions not in workers:
            sizes = dv_partition_bases(regions, partitions)
            workers[partitions] = (
                None if sizes is None else dv_postprocess_worker_bytes(max(sizes))
            )
        return workers[partitions]

    default = worker(threads)
    if default is None:
        # DeepVariant runs (or fails) as it would have; keep its default.
        total = sum(end - start for _, start, end in regions)
        return PostprocessPlan(
            threads, threads, dv_postprocess_worker_bytes(total), None
        )
    if available is None:
        return PostprocessPlan(threads, threads, default, None)
    budget = available - GIB
    limit = max(DV_MAX_PARTITIONS, threads)
    for cpus in range(threads, 0, -1):
        for count in range(cpus, (limit if partitions else cpus) + 1):
            need = worker(count)
            if need is not None and cpus * need <= budget:
                return PostprocessPlan(cpus, count, need, True)
    smallest = min(
        range(1, (limit if partitions else 1) + 1),
        key=lambda count: worker(count) or math.inf,
    )
    return PostprocessPlan(1, smallest, worker(smallest) or default, False)


def _disk_space(path: str) -> tuple[object, int | None]:
    existing = Path(path).expanduser().resolve()
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    try:
        return existing.stat().st_dev, shutil.disk_usage(existing).free
    except OSError:
        return str(existing), None


def _input_size(paths: Sequence[str]) -> int | None:
    try:
        if not paths or any(not Path(path).is_file() for path in paths):
            return None
        return sum(Path(path).stat().st_size for path in dict.fromkeys(paths))
    except OSError:
        return None


@dataclass(frozen=True)
class LongReadDisk:
    """Peak bytes of a long-read minimap2 alignment: the sorter's spills, the
    sorted output and, when kept, the tagged FASTQ.gz extracted from a BAM/CRAM."""

    spill: int
    output: int
    fastq: int


def long_read_cram_bytes(input_path: str, input_bytes: int) -> float:
    """The size of long reads' ``input_bytes`` as CRAM."""
    name = input_path.lower()
    kind = "cram" if name.endswith(".cram") else "bam" if name.endswith(".bam") else ""
    if not kind:
        # Read files are gzip/BGZF compressed whatever their names.
        try:
            with open(input_path, "rb") as handle:
                kind = "gz" if handle.read(2) == b"\x1f\x8b" else "plain"
        except OSError:
            kind = "gz" if name.endswith((".gz", ".bgz", ".bgzf")) else "plain"
    return max(0, input_bytes) / _LONG_READ_BYTES_PER_CRAM_BYTE.get(kind, 1)


def long_read_disk(
    input_path: str, input_bytes: int, output_format: str, sort_buffer_bytes: float
) -> LongReadDisk:
    """Scale by the input's size, not by short-read per-read sizes: one long
    read takes tens of kilobytes. ``sort_buffer_bytes`` is -m times threads."""
    cram = long_read_cram_bytes(input_path, input_bytes)
    spill = cram * LONG_READ_SPILL_PER_CRAM_BYTE
    merging = _SORT_CONSOLIDATED_FILES * sort_buffer_bytes * _SORT_SPILL_PER_BUFFER_BYTE
    return LongReadDisk(
        math.ceil(spill + min(spill, merging)),
        math.ceil(cram * LONG_READ_OUTPUT_PER_CRAM_BYTE.get(output_format, 2.6)),
        math.ceil(cram * LONG_READ_FASTQ_PER_CRAM_BYTE),
    )


def bcftools_batches(contigs: dict[str, int], workers: int) -> list[dict[str, int]]:
    """Consecutive contigs grouped into the same jobs as the calling pipeline."""
    limit = max(1, sum(contigs.values()) // (workers * _BCFTOOLS_JOBS_PER_WORKER))
    batches: list[dict[str, int]] = []
    batch: dict[str, int] = {}
    for name, length in contigs.items():
        if length >= limit:
            batches += [batch] if batch else []
            batches.append({name: length})
            batch = {}
            continue
        batch[name] = length
        if sum(batch.values()) >= limit:
            batches.append(batch)
            batch = {}
    return batches + ([batch] if batch else [])


# The partition outputs, their gathered copy and the published gVCF can coexist.
GVCF_COPIES = 3
