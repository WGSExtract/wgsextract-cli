"""Memory and disk estimates for alignment, conversion and variant calling."""

import json
import math
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from . import conversion_resources as _conversion
from ._resource_inputs import (
    _BCFTOOLS_GVCF_BYTES_PER_READ,
    _BCFTOOLS_PART_HEADER_BYTES,
    _BCFTOOLS_VCF_BYTES_PER_READ,
    BAM_BYTES_PER_READ,
    CRAM_BYTES_PER_READ,
    DV_GVCF_PER_INPUT_BYTE,
    FASTQ_BYTES_PER_READ,
    GATK_GVCF_BYTES_PER_READ,
    GATK_WORKER_BYTES,
    GIB,
    GVCF_COPIES,
    NATIVE_DV_BASE_BYTES,
    NATIVE_DV_CONTIG_BYTES_PER_BASE,
    NATIVE_DV_EXAMPLES_PER_INPUT_BYTE,
    NATIVE_DV_GVCF_BYTES_PER_BASE,
    NATIVE_DV_GVCF_TEMPORARY_PER_INPUT_BYTE,
    NATIVE_DV_INFERENCE_BYTES_PER_EXAMPLE,
    NATIVE_DV_PHASE_MERGE_BYTES_PER_INPUT_BYTE,
    NATIVE_DV_RECORD_BYTES_PER_BASE,
    NATIVE_DV_THREAD_BYTES,
    SORT_BYTES_PER_READ,
    Operation,
    PostprocessPlan,
    PreflightEstimate,
    ResourceEstimate,
    _disk_space,
    _input_size,
    bcftools_batches,
    dv_calling_regions,
    long_read_disk,
    plan_dv_postprocess,
)
from .alignment import memory as alignment_memory
from .alignment.samtools_commands import parse_memory
from .native_support import is_arm64_docker_host
from .ref_library.assets import read_fai_lengths
from .resource_volumes import reference_evidence_demands


def estimate_resources(
    operation: Operation,
    *,
    input_paths: Sequence[str],
    output_dir: str,
    reference: str | None = None,
    threads: str = "1",
    alignment_threads: str | None = None,
    memory: str = "512M",
    aligner: str = "bwa",
    preset: str | None = None,
    reads: int | None = None,
    platform: str | None = None,
    read_length: float | None = None,
    output_format: str = "BAM",
    samblaster: bool = True,
    extract_fastq: bool = False,
    fastq_dir: str | None = None,
    temp_dir: str | None = None,
    gatk_staging_dir: str | None = None,
    region: str | None = None,
    duplicate_marking: Literal["samblaster", "markdup", "gatk-flow"] | None = None,
    parallel: bool = False,
    gatk_workers: int = 1,
    additional_ram_bytes: int = 0,
    gpu_bytes: int = 0,
    dense_sa_bytes: int = 0,
    inference_batch_size: int = 1024,
    deepvariant_model: str = "WGS",
    postprocess_plan: PostprocessPlan | None = None,
    available_ram: int | None = None,
    deepvariant_native: bool = False,
    bcftools_contigs: dict[str, int] | None = None,
    alignment_header: str | None = None,
    staged: bool = False,
    keep_fastq: bool = False,
    retire_fastqs: tuple[str, ...] = (),
    staged_workspace: str | None = None,
    reference_evidence: bool = False,
) -> PreflightEstimate:
    """Estimate a resolved command without creating files or running tools.

    ``bcftools_contigs`` is the caller's resolved parallel plan (empty for a
    serial fallback); None conservatively infers it from the reference index.
    ``alignment_header`` helps budget full headers without launching metadata
    tools in this read-only API. ``reference_evidence`` adds the disk of
    ``vcf snp``/``vcf indel``'s gVCF and its per-contig parts, GATK's gVCF
    and its partition copies, or DeepVariant's gVCF and the memory and work
    it takes.

    ``reads`` counts individual reads, not pairs. Callers may provide a cheap
    FASTQ sample/indexed count. Otherwise disk uses a conservative byte-size
    fallback; alignment RAM retains the D3 model's explicit unknown-count budget.
    Regional operations retain whole-input disk bounds, not guessed coverage.
    Callers that already planned DeepVariant's postprocess_variants pass that
    ``postprocess_plan`` and the ``available_ram`` it was sized against (for
    example capped by Docker's VM); otherwise both are derived here.
    """
    count = int(threads)
    if (
        count < 1
        or gatk_workers < 1
        or (reads is not None and reads < 0)
        or additional_ram_bytes < 0
        or inference_batch_size < 1
    ):
        raise ValueError(
            "threads/workers/batch size must be positive and reads/additional RAM nonnegative"
        )
    amount, unit = parse_memory(memory)
    sort_buffer = count * amount * (GIB if unit == "G" else 1024**2)
    sort_memory = math.ceil(sort_buffer * 1.25)
    size = _input_size(input_paths)
    cram_input = bool(input_paths) and input_paths[0].lower().endswith(".cram")
    bam_input = bool(input_paths) and input_paths[0].lower().endswith(".bam")
    notes = [
        "Conservative estimates, not allocations or hard limits; existing inputs are excluded."
    ]
    if region:
        notes.append("Regional disk estimates use the whole input as an upper bound.")
    long_read = (
        long_read_disk(input_paths[0], size, output_format, sort_buffer)
        if operation in {"align", "realign"}
        and aligner == "minimap2"
        and preset not in {None, "sr"}
        and size is not None
        else None
    )
    disk_reads = reads
    if disk_reads is None and size is not None and long_read is None:
        bytes_per_read = 12 if cram_input else (80 if bam_input else 70)
        disk_reads = math.ceil(size / bytes_per_read)
        notes.append(
            "Disk read count inferred from input bytes; compression and read lengths vary."
        )
    available_memory: int | None = available_ram
    if available_memory is None:
        try:
            available_memory = alignment_memory.available_memory()
        except OSError:
            available_memory = None
    demands: list[tuple[str, str, int | None]] = []
    staged_phases: list[list[tuple[str, str, int | None]]] | None = None
    memory_components: dict[str, int]
    memory_unknown = False
    output: int | None
    temporary: int | None = 0
    if operation in {"align", "realign"}:
        if not reference:
            notes.append(
                "Reference unresolved: RAM uses the human-reference fallback; reference-index disk is unknown."
            )
        mapping = alignment_memory.estimate_mapping_memory(
            reference,
            aligner,
            threads=threads,
            alignment_threads=alignment_threads,
            memory=memory,
            samblaster=samblaster and duplicate_marking != "gatk-flow",
            reads=reads,
            platform=platform,
            read_length=read_length,
            preset=preset,
            input_bytes=size if aligner == "minimap2" else None,
            gpu_bytes=gpu_bytes,
            dense_sa_bytes=dense_sa_bytes,
            staged=staged,
        )
        if gpu_bytes:
            notes.append(
                f"Working memory includes {gpu_bytes / 1024**3:.1f} GiB for the "
                "aligner's GPU backend (Metal or CUDA)."
            )
        if dense_sa_bytes:
            notes.append(
                f"Index memory includes {dense_sa_bytes / 1024**3:.1f} GiB for "
                "wgse-bwa's dense suffix-array file."
            )
        if aligner == "minimap2":
            notes.append(
                f"minimap2 builds its {preset or 'largest (sr)'} index in memory each "
                "run; working memory is its build peak or its mapping batches."
            )
        if aligner == "bwa":
            length = (
                read_length
                if read_length is not None
                else (300 if platform == "ultima" else 150)
            )
            notes.append(
                f"BWA working memory uses {platform or 'short-read'} / {length:g} bp "
                f"({'sampled/provided' if read_length is not None else 'fallback'}), "
                "fixed -K 100000000; thread budgets follow the resolved stage plan."
            )
        memory_components = dict(zip(mapping._fields, mapping, strict=True))
        if additional_ram_bytes:
            memory_components["collation"] = additional_ram_bytes
            notes.append(
                "Streamed FASTQ collation overlaps the alignment pipeline; its "
                "spills drain as the sorter's grow, within the temporary estimate."
            )
        if reads is None and (aligner != "minimap2" or samblaster):
            notes.append(
                "Alignment RAM uses the D3 680-million-read unknown-count fallback."
            )
        output = (
            None
            if disk_reads is None
            else disk_reads
            * (CRAM_BYTES_PER_READ if output_format == "CRAM" else BAM_BYTES_PER_READ)
        )
        temporary = None if disk_reads is None else disk_reads * SORT_BYTES_PER_READ
        if staged and disk_reads is not None:
            scale = max(1, (read_length or 150) / 150)
            raw = math.ceil(disk_reads * 108 * scale)
            parts = math.ceil(disk_reads * 115 * scale)
            merge_group = min(parts, math.ceil(64 * 2_000_000 * 115 * scale))
            scratch = 2 * GIB
            temporary = raw + parts + scratch
            place = temp_dir or output_dir
            fq: list[tuple[str, str, int | None]] = (
                [
                    (
                        fastq_dir or output_dir,
                        "extracted_fastqs",
                        disk_reads * FASTQ_BYTES_PER_READ,
                    )
                ]
                if extract_fastq
                else []
            )
            staged_retained = fq if keep_fastq else []
            staged_phases = [
                [*fq, (place, "raw_alignment", raw)],
                [
                    *staged_retained,
                    (place, "raw_alignment", raw),
                    (place, "sorted_parts", parts),
                    (place, "rolling_sort", scratch),
                ],
                [
                    *staged_retained,
                    (place, "sorted_parts", parts),
                    (place, "merge_generation", merge_group),
                ],
                [
                    *staged_retained,
                    (place, "sorted_parts", parts),
                    (output_dir, "output", output),
                ],
            ]
            if extract_fastq:
                from wgsextract_cli.core.warnings import get_free_space_needed

                spill, final = get_free_space_needed(
                    size or 0, sort_type="Name", is_cram=cram_input, reads=disk_reads
                )
                staged_phases.insert(
                    0,
                    [
                        (place, "extraction_collate", spill * GIB),
                        (fastq_dir or output_dir, "extracted_fastqs", final * GIB),
                    ],
                )
            if staged_workspace:
                work = Path(staged_workspace)
                checkpoint = work / "parts.json"
                complete = False
                if checkpoint.is_file():
                    try:
                        saved_parts = json.loads(checkpoint.read_text())
                        complete = (
                            isinstance(saved_parts, dict)
                            and saved_parts.get("complete") is True
                        )
                    except (OSError, ValueError):
                        pass
                if complete:
                    staged_phases = [
                        fq,
                        [*staged_retained, (place, "merge_generation", merge_group)],
                        [*staged_retained, (output_dir, "output", output)],
                    ]
                elif (work / "alignment.json").is_file() and (
                    work / "unsorted.bam"
                ).is_file():
                    credit = (work / "unsorted.bam").stat().st_size
                    for phase in staged_phases:
                        phase.append((str(work), "existing_checkpoint", -credit))
            for path in retire_fastqs:
                if Path(path).is_file():
                    for phase in staged_phases[1:]:
                        phase.append(
                            (path, "retired_fastqs", -Path(path).stat().st_size)
                        )
            notes.append(
                "Staged Metal peaks are per phase, not concurrent sums: alignment "
                "has no samblaster/sort; compressed marked parts replace the raw BAM "
                "before final merge. Generated FASTQs retire after alignment unless kept. "
                "Disk allows 108/115 bytes per short input read for raw/marked checkpoints "
                "and 2 GiB rolling scratch (HG002 35x calibration)."
            )
            from wgsextract_cli.core.staged_alignment import safety_reserve

            reserve = safety_reserve(reads)
            for phase in staged_phases:
                devices: set[object] = set()
                for path in (place, output_dir):
                    device, _ = _disk_space(path)
                    if device not in devices:
                        phase.append((path, "disk_safety_reserve", reserve))
                        devices.add(device)
        if long_read is not None:
            output, temporary = long_read.output, long_read.spill
            notes.append(
                "Long-read disk scales with the input's size: minimap2 -Y keeps "
                "each supplementary alignment's full read and tags."
            )
        if duplicate_marking == "gatk-flow":
            if disk_reads is not None:
                retained = (
                    disk_reads
                    * BAM_BYTES_PER_READ
                    * (2 if output_format == "CRAM" else 1)
                )
                temporary = max(temporary or 0, retained)
            notes.append(
                "GATK flow duplicate marking includes retained sorted/marked BAMs."
            )
        if extract_fastq:
            demands.append(
                (
                    fastq_dir or output_dir,
                    "extracted_fastqs",
                    long_read.fastq
                    if long_read is not None
                    else None
                    if disk_reads is None
                    else disk_reads * FASTQ_BYTES_PER_READ,
                )
            )
        index_suffixes = {
            "bwa": (".bwt", ".sa", ".pac", ".ann", ".amb"),
            "bwa-mem2": (".0123", ".bwt.2bit.64", ".pac", ".ann", ".amb"),
            "bwa-mem3": (".bwt.2bit.64", ".pac", ".ann", ".amb"),
            "wgse-bwa-mem2": (".bwt.2bit.64", ".pac", ".ann", ".amb"),
        }
        if reference and aligner in index_suffixes:
            suffixes = index_suffixes[aligner]
            if not all(Path(reference + suffix).is_file() for suffix in suffixes):
                demands.append(
                    (
                        str(Path(reference).parent),
                        "reference_indexes",
                        (
                            alignment_memory.estimate_mapping_memory(
                                reference,
                                aligner,
                                threads=threads,
                                memory=memory,
                                samblaster=samblaster,
                                reads=reads,
                            ).index_resident
                            if staged
                            else mapping.index_resident
                        )
                        * 2,
                    )
                )
                notes.append(
                    "Missing reference indexes include an additional build-space allowance."
                )
    elif operation == "sort":
        memory_components = {"sort": sort_memory, "overhead": GIB // 4}
        output = (
            None
            if disk_reads is None
            else disk_reads
            * (CRAM_BYTES_PER_READ if output_format == "CRAM" else BAM_BYTES_PER_READ)
        )
        temporary = None if disk_reads is None else disk_reads * SORT_BYTES_PER_READ
    elif operation in {"to-bam", "to-cram"}:
        memory_components, output, memory_unknown = _conversion.conversion_resources(
            count, disk_reads, reference, region, operation, notes
        )
    elif operation == "deepvariant" and deepvariant_native:
        calling = dv_calling_regions(
            read_fai_lengths(reference) if reference else {}, region
        )
        contig_bases: dict[str, int] = {}
        for contig, start, end in calling or []:
            contig_bases[contig] = contig_bases.get(contig, 0) + end - start
        memory_components = {
            "largest_sequential_stage": max(
                NATIVE_DV_BASE_BYTES + count * NATIVE_DV_THREAD_BYTES,
                NATIVE_DV_BASE_BYTES
                + inference_batch_size * NATIVE_DV_INFERENCE_BYTES_PER_EXAMPLE,
                NATIVE_DV_BASE_BYTES
                + math.ceil(
                    sum(contig_bases.values())
                    * (
                        NATIVE_DV_RECORD_BYTES_PER_BASE
                        + (NATIVE_DV_GVCF_BYTES_PER_BASE if reference_evidence else 0)
                    )
                    + max(contig_bases.values(), default=0)
                    * NATIVE_DV_CONTIG_BYTES_PER_BASE
                ),
                NATIVE_DV_BASE_BYTES
                + (
                    math.ceil((size or 0) * NATIVE_DV_PHASE_MERGE_BYTES_PER_INPUT_BYTE)
                    if deepvariant_model == "ONT_R104"
                    else 0
                ),
            ),
            "driver_headroom": GIB,
        }
        notes.append(
            "wgse-deepvariant runs make_examples, call_variants (memory set "
            "by --batch-size) and postprocess_variants (one contig at a "
            "time) one after another: allow for the largest."
        )
        if reference_evidence:
            notes.append(
                "With the gVCF, wgse-deepvariant's postprocess_variants keeps "
                "every contig's calls until it writes it: ~5 more bytes per "
                "called base (~15 GiB for a genome)."
            )
        output = None if size is None else math.ceil(size / 10)
        temporary = (
            None
            if size is None
            else math.ceil(
                size
                * (
                    NATIVE_DV_EXAMPLES_PER_INPUT_BYTE
                    + (
                        NATIVE_DV_GVCF_TEMPORARY_PER_INPUT_BYTE
                        if reference_evidence
                        else 0
                    )
                )
            )
        )
    elif operation == "deepvariant":
        arm64 = is_arm64_docker_host()
        inference = 2 * GIB + math.ceil(
            (30 if arm64 else 14) * GIB * inference_batch_size / 1024
        )
        sharded = count * (2 if arm64 else 1) * GIB
        # run_deepvariant postprocesses WES in one sequential pass (--cpus 0).
        regions = (
            None
            if deepvariant_model == "WES"
            else dv_calling_regions(
                read_fai_lengths(reference) if reference else {}, region
            )
        )
        plan = postprocess_plan or (
            None
            if regions is None
            else plan_dv_postprocess(regions, count, available_memory)
        )
        memory_components = {
            "largest_sequential_stage": max(
                inference, sharded, plan.peak_bytes if plan else 0
            ),
            "driver_headroom": GIB,
        }
        notes.append(
            "DeepVariant WGS uses sequential stages: allow for the largest of "
            "TensorFlow inference, concurrent make_examples shards and "
            "postprocess_variants partition sorts, not their sum. "
            "Reducing --threads does not remove the default inference memory floor; "
            "other models/checkpoints may require more RAM."
        )
        if regions is None and deepvariant_model != "WES":
            notes.append(
                "postprocess_variants RAM is unknown without a reference .fai."
            )
        elif plan is not None:
            notes.append(
                f"postprocess_variants: {plan.cpus} workers over {plan.partitions} "
                f"partitions, up to {plan.worker_bytes / GIB:.2f} GiB each "
                "(calibrated on 30x Ultima call density)."
            )
        output = None if size is None else math.ceil(size / 10)
        temporary = size
        notes.append(
            "DeepVariant temporary disk allows one input-sized pileup/intermediate "
            "working set; actual coverage and candidate density can change it."
        )
    else:
        if operation == "gatk":
            memory_components = {"caller": gatk_workers * GATK_WORKER_BYTES}
            notes.append(
                "GATK RAM is a heuristic; Java heap and flow-mode settings can change its peak."
            )
            if cram_input and region:
                demands.append(
                    (
                        gatk_staging_dir or output_dir,
                        "regional_bam",
                        None if disk_reads is None else disk_reads * BAM_BYTES_PER_READ,
                    )
                )
        else:
            contigs = read_fai_lengths(reference) if reference else {}
            selected = contigs if bcftools_contigs is None else bcftools_contigs
            unknown_parallel = (
                parallel
                and count > 1
                and not region
                and bcftools_contigs is None
                and not contigs
            )
            active = parallel and count > 1 and not region and len(selected) > 1
            batches = (
                bcftools_batches(selected, min(count, len(selected))) if active else []
            )
            workers = 1
            if unknown_parallel:
                workers = count
            elif batches:
                workers = min(count, len(batches))
            memory_components = {"callers": workers * GIB, "overhead": GIB // 4}
            notes.append(
                "bcftools RAM allows 1 GiB per active contig worker plus overhead."
            )
        if operation in _BCFTOOLS_VCF_BYTES_PER_READ:
            reference_header = "".join(
                f"##contig=<ID={name},length={length}>\n"
                for name, length in contigs.items()
            ).encode()
            input_header = (alignment_header or "").encode()
            # Include the complete contig dictionary, not a fixed allowance
            # per shard. Extra room covers INFO/FORMAT declarations and BCF IDs.
            raw_header = (
                2 * len(reference_header)
                + len(input_header)
                + 4 * _BCFTOOLS_PART_HEADER_BYTES
            )
            compressed_header = (
                2 * len(zlib.compress(reference_header + input_header))
                + _BCFTOOLS_PART_HEADER_BYTES
            )
            output = (
                None
                if disk_reads is None
                else max(
                    64 * 1024,
                    compressed_header,
                    math.ceil(disk_reads * _BCFTOOLS_VCF_BYTES_PER_READ[operation]),
                )
            )
            temporary = None if unknown_parallel else 0
            if unknown_parallel:
                notes.append(
                    "Parallel bcftools temporary disk is unknown without a "
                    "reference index or resolved calling contigs."
                )
            if batches:
                replay = operation == "snp"
                compressed_copies = len(batches) + (3 if replay else 1)
                split_contigs = sum(len(batch) for batch in batches if len(batch) > 1)
                # Split batches, window splices, whole-contig fallback splices,
                # and the reheadering text can coexist until final publication.
                raw_copies = split_contigs + 2 * len(selected) + 1 if replay else 0
                requests = sum(len(name.encode()) + 32 for name in selected)
                temporary = (
                    None
                    if output is None
                    else (8 if replay else 2) * output
                    + compressed_copies * compressed_header
                    + raw_copies * raw_header
                    + requests
                )
                notes.append(
                    f"bcftools budgets {len(batches)} compressed batches and "
                    f"{raw_copies} possible full uncompressed replay headers "
                    f"({raw_header} bytes each)."
                )
            if reference_evidence:
                gvcf = (
                    None
                    if disk_reads is None
                    else max(
                        64 * 1024,
                        math.ceil(disk_reads * _BCFTOOLS_GVCF_BYTES_PER_READ),
                    )
                )
                # Per-contig parts coexist with the merged gVCF until it's published.
                demands += [
                    (output_dir, "reference_evidence", gvcf),
                    (temp_dir or output_dir, "reference_evidence_parts", gvcf),
                ]
            notes.append(
                "bcftools compressed VCF disk uses the HG002 30x envelope "
                "(BAM/CRAM read-count normalization); serial calling writes "
                "directly to output with no parts. Depth, variant burden and "
                "compression vary."
            )
        else:
            output = None if size is None else math.ceil(size / 10)
            temporary = (
                None if output is None else output * (2 if operation == "gatk" else 1)
            )
    if operation == "deepvariant" and reference_evidence:
        gvcf_bytes = None if size is None else math.ceil(size * DV_GVCF_PER_INPUT_BYTE)
        demands.append((output_dir, "reference_evidence", gvcf_bytes))
    if operation == "gatk" and reference_evidence:
        gvcf_bytes = (
            None
            if disk_reads is None
            else math.ceil(disk_reads * GATK_GVCF_BYTES_PER_READ)
        )
        demands += reference_evidence_demands(
            output_dir, gatk_staging_dir, gvcf_bytes, GVCF_COPIES
        )
    indexes = None if output is None else max(64 * 1024, math.ceil(output / 100))
    if operation == "gatk" and gatk_workers > 1:
        # Shards coexist with the gathered output.
        demands += [
            (gatk_staging_dir or output_dir, "scatter_parts", output),
            (gatk_staging_dir or output_dir, "scatter_indexes", indexes),
        ]
    final_demands = [
        (output_dir, "output", output),
        (output_dir, "indexes", indexes),
        (
            temp_dir or output_dir,
            "temporary",
            temporary,
        ),
    ]
    if staged_phases is None:
        demands += final_demands
    else:
        demands = [entry for entry in demands if entry[1] != "extracted_fastqs"]
        for phase in staged_phases:
            phase.append((output_dir, "indexes", indexes))
    grouped: dict[object, tuple[int | None, dict[str, int], list[str], bool]] = {}
    for path, component, needed in demands:
        if needed == 0:
            continue
        device, available = _disk_space(path)
        if device not in grouped:
            grouped[device] = (available, {}, [], False)
        old_available, components, paths, unknown = grouped[device]
        if path not in paths:
            paths.append(path)
        if needed is not None:
            components[component] = components.get(component, 0) + needed
        free = (
            min(old_available, available)
            if old_available is not None and available is not None
            else None
        )
        grouped[device] = (free, components, paths, unknown or needed is None)
    if staged_phases is not None:
        for phase in staged_phases:
            totals: dict[object, int | None] = {}
            for path, _, needed in phase:
                device, available = _disk_space(path)
                if device not in grouped:
                    grouped[device] = (available, {}, [], False)
                old_available, components, paths, unknown = grouped[device]
                if path not in paths:
                    paths.append(path)
                total = totals.get(device, 0)
                totals[device] = (
                    None if total is None or needed is None else total + needed
                )
                free = (
                    min(old_available, available)
                    if old_available is not None and available is not None
                    else None
                )
                grouped[device] = (free, components, paths, unknown or needed is None)
            for device, total in totals.items():
                if total is not None:
                    grouped[device][1]["staged_peak"] = max(
                        grouped[device][1].get("staged_peak", 0), total
                    )
    resources = [
        ResourceEstimate(
            "memory",
            None if memory_unknown else sum(memory_components.values()),
            available_memory,
            memory_components,
        )
    ]
    resources += [
        ResourceEstimate(
            "disk",
            None if unknown else sum(components.values()),
            available,
            components,
            tuple(paths),
        )
        for available, components, paths, unknown in grouped.values()
    ]
    if operation in {"align", "realign"} and not reference:
        resources.append(ResourceEstimate("disk", None, None, {}))
    return PreflightEstimate(operation, tuple(resources), tuple(notes))
