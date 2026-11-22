"""Alignment short reads helpers."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from wgsextract_cli.core import bwa_mem2, dense_suffix_array
from wgsextract_cli.core.alignment import bwa_index
from wgsextract_cli.core.alignment.memory import (
    BWA_MEM3_CUDA_BYTES,
    BWA_MEM3_METAL_BYTES,
    MINIMAP2_CUDA_BYTES,
    available_memory,
    bwa_gpu_memory,
    estimate_mapping_memory,
    windows_available_commit,
)
from wgsextract_cli.core.alignment.samtools_commands import parse_memory
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.native_aligners import (
    BWA_MEM3_COMPAT,
    BWA_MEM3_FAST,
    INSTALL_HINT,
    AlignerCommand,
    NativeAligner,
    NativeMemoryDecision,
    bwa_mem3_failure,
    find_bwa_mem3,
    find_native_aligner,
    native_gpu_preference,
    native_setting,
    on_windows_x64,
    platform_binaries,
)
from wgsextract_cli.core.pixi_tools import pixi_install_command
from wgsextract_cli.core.resource_policy import stage_thread_plan
from wgsextract_cli.core.utils import (
    UsageError,
    WGSExtractError,
)

from .selection import (
    requested_aligner,
)

if TYPE_CHECKING:
    pass

from .common import (
    AlignJob,
    ensure_fasta_index,
)


def short_read_aligner(
    requested: str,
    ref: str,
    *,
    threads: str = "1",
    alignment_threads: str | None = None,
    memory: str = "512M",
    samblaster: bool = True,
    reads: int | None = None,
    native_aligners: str | None = "off",
) -> str:
    """bwa or bwa-mem2 for --aligner bwa, bwa-mem2 or auto. Auto takes bwa-mem2,
    which is faster than bwa but writes different records, when it will run
    (upstream bwa-mem2, or with ``native_aligners`` wgse-bwa-mem2 0.5.0 and
    later, which needs no upstream bwa-mem2) and the reference has the index
    it reads that fits in this computer's memory (that build's estimate leaves
    out the .0123 file; AlignerCommand.memory_model)."""
    if requested != "auto":
        return requested
    model = bwa_mem2_memory_model(native_aligners)
    if model == "bwa-mem2" and get_tool_path("bwa-mem2") is None:
        return "bwa"
    memory = short_read_sort_memory("bwa-mem2", memory, log=False)
    needed = estimate_mapping_memory(
        ref,
        model,
        threads=threads,
        alignment_threads=alignment_threads,
        memory=memory,
        samblaster=samblaster,
        reads=reads,
    ).total
    fits = available_memory() >= needed
    if bwa_mem2_index_ready(model, ref):
        if fits:
            if model == "wgse-bwa-mem2":
                logging.info(
                    "Auto chose bwa-mem2 (records differ from bwa's): "
                    "wgse-bwa-mem2 0.5.0 or later maps without the .0123 file "
                    f"and fits in {needed / 1024**3:.1f} GiB. Use --aligner bwa "
                    "to keep bwa's records."
                )
            return "bwa-mem2"
        logging.info(
            f"Not using {model}: the alignment pipeline needs about "
            f"{needed / 1024**3:.1f} GiB of available RAM. Use --aligner bwa "
            "or lower --memory."
        )
    elif fits:
        logging.info(
            "bwa-mem2 would align this about a third faster; "
            f"`wgsextract ref index --aligner bwa-mem2 --ref {ref}` sets it up."
        )
    return "bwa"


def short_read_command(
    job: AlignJob,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
    announce: bool = True,
) -> tuple[AlignerCommand, str]:
    """How to run --aligner bwa, bwa-mem2 or auto (short_read_aligner's choice,
    or an Apple Silicon build in its place; native_short_read_aligner), and
    the sort memory per thread to use. Without ``announce``, a plan made
    before the alignment (realign's) logs less."""
    tool = short_read_aligner(
        requested_aligner(job),
        ref,
        threads=threads,
        alignment_threads=str(stage_thread_plan(job.threads).alignment),
        memory=memory,
        samblaster=samblaster,
        reads=reads,
        native_aligners=job.native_aligners,
    )
    if tool == "bwa-mem2":
        ensure_fasta_index(ref)
    return native_short_read_aligner(
        job,
        tool,
        ref,
        threads=threads,
        memory=short_read_sort_memory(tool, memory, log=announce),
        samblaster=samblaster,
        reads=reads,
        read_length=read_length,
        additional_ram_bytes=additional_ram_bytes,
        announce=announce,
    )


def stand_in_label(aligner: AlignerCommand) -> str | None:
    """How memory checks name a build running in another aligner's place."""
    if aligner.engine == aligner.tool:
        return None
    return f"{aligner.binary} (in place of {aligner.tool})"


def streaming_mapping_fits(
    ref: str,
    tool: str,
    *,
    threads: str,
    alignment_threads: str | None = None,
    memory: str,
    samblaster: bool,
    reads: int | None,
    platform: str | None,
    read_length: float | None,
    additional_ram_bytes: int,
    gpu_bytes: int = 0,
    dense_sa_bytes: int = 0,
    staged: bool = False,
) -> bool:
    estimate = estimate_mapping_memory(
        ref,
        tool,
        threads=threads,
        alignment_threads=alignment_threads,
        memory=short_read_sort_memory(tool, memory, log=False),
        samblaster=samblaster,
        reads=reads,
        platform=platform,
        read_length=read_length,
        gpu_bytes=gpu_bytes,
        dense_sa_bytes=dense_sa_bytes,
        staged=staged,
    )
    return estimate.total + additional_ram_bytes <= available_memory()


def short_read_sort_memory(aligner: str, memory: str, *, log: bool = True) -> str:
    """Keep more memory for MEM2's index without exceeding the user's budget."""
    if aligner != "bwa-mem2":
        return memory
    amount, unit = parse_memory(memory)
    if amount * (1024 if unit == "G" else 1) > 256:
        if log:
            logging.info(
                f"Capping bwa-mem2 sort memory at 256M per thread (budget {memory})."
            )
        return "256M"
    return memory


# Memory left for the OS and other applications when wgse-bwa's GPU backend
# (Metal or CUDA) must fit.
_GPU_HEADROOM_BYTES = 2 * 1024**3
_GPU_SORT_MEMORY_STEPS_MIB = (768, 512, 384, 256)


def native_short_read_aligner(
    job: AlignJob,
    tool: str,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
    announce: bool = True,
) -> tuple[AlignerCommand, str]:
    """How to run bwa or bwa-mem2, and the sort memory per thread to use.

    Its build (wgse-bwa, wgse-bwa-mem2) writes the same alignments; it runs on
    its GPU (Metal on Apple Silicon, CUDA on Linux or Windows) when it can. wgse-bwa's GPU
    backend needs bwa_gpu_memory more RAM: when the pipeline's estimate with it
    exceeds physical RAM less headroom or the RAM available now, sort memory
    is lowered without changing the output. When even 256M doesn't fit,
    wgse-bwa runs on the CPU, whose buffers are not wired for GPU access.
    An over-budget CPU fallback also caps sort memory at 256M per thread.
    Workers and the scientific batch size are unchanged. Streaming realign
    counts additional_ram_bytes beside the same pipeline.
    wgse-bwa-mem2 keeps bwa-mem2's (CPU) estimate, which also covers its CPU
    fallback when the GPU can't hold its index tables; from 0.5.0 that
    estimate leaves out the .0123 file it no longer loads
    (AlignerCommand.memory_model).
    bwa-mem3 (wgse-bwa-mem3 on Apple Silicon, Bioconda's on Linux) writes
    either's exact records faster, so it runs in their place when it fits
    (bwa_mem3_stand_in), ahead of a GPU build.
    wgse-bwa reads the reference's dense suffix-array file only when the run
    asks (job.dense_suffix_array, dense_suffix_array.for_alignment); it
    counts as index memory.
    """

    # bwa-mem3 goes first, then the build (on its GPU when it fits), then
    # upstream. Even on Linux with an NVIDIA GPU: on an RTX 3090 and a Ryzen 5
    # 5600X, bwa-mem3 --compat=bwa-mem on the CPU aligned 5M pairs in 98 s
    # against wgse-bwa --cuda's 131 s, with the same records (#736).
    stand_in = bwa_mem3_stand_in(
        job,
        tool,
        ref,
        threads=threads,
        memory=memory,
        samblaster=samblaster,
        reads=reads,
        read_length=read_length,
        additional_ram_bytes=additional_ram_bytes,
    )
    dense = bool(job.dense_suffix_array)
    if stand_in is not None:
        if announce:
            dense_suffix_array.other_aligner(stand_in.binary, enabled=dense)
            stand_in.announce()
        return stand_in, memory
    native = find_native_aligner(tool, job.native_aligners)
    if tool != "bwa":
        if announce:
            aligner_name = native.binary if native else tool
            dense_suffix_array.other_aligner(aligner_name, enabled=dense)
        dense = False
    dense_sa = dense_suffix_array.for_alignment(native, ref, enabled=dense)
    if native is None:
        return AlignerCommand(tool), memory
    if native.gpu is None:
        aligner = AlignerCommand(tool, native, dense_sa=dense_sa)
    elif tool != "bwa":
        aligner = AlignerCommand(tool, native, gpu=True)
    elif (
        native.gpu == "cuda"
        and on_windows_x64()
        and not native_gpu_preference(job.prefer_native_gpu)
    ):
        if announce:
            logging.info(
                "Windows CUDA is opt-in: --prefer-native-gpu enables it when "
                "the alignment fits. Using wgse-bwa on the CPU."
            )
        aligner = AlignerCommand(tool, native, dense_sa=dense_sa)
    else:
        aligner, memory = _bwa_gpu_fit(
            job,
            native,
            ref,
            threads=threads,
            memory=memory,
            samblaster=samblaster,
            reads=reads,
            read_length=read_length,
            additional_ram_bytes=additional_ram_bytes,
            dense_sa=dense_sa,
        )
    if announce:
        aligner.announce()
    return aligner, memory


def _bwa_gpu_fit(
    job: AlignJob,
    native: NativeAligner,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int,
    dense_sa: Path | None,
) -> tuple[AlignerCommand, str]:
    """wgse-bwa on its GPU with the sort memory per thread that lets it fit
    (see native_short_read_aligner), else on the CPU with bounded sort memory."""
    tool = "bwa"
    gpu = bwa_gpu_memory(ref, reads, dense_sa_file=dense_sa is not None)
    dense_sa_bytes = dense_sa.stat().st_size if dense_sa else 0
    budget = _gpu_budget(additional_ram_bytes)
    windows_cuda = native.gpu == "cuda" and on_windows_x64()
    commit = windows_available_commit() if windows_cuda else None
    amount, unit = parse_memory(memory)
    mib = amount * (1024 if unit == "G" else 1)
    candidates = [memory] + [
        f"{step}M" for step in _GPU_SORT_MEMORY_STEPS_MIB if step < mib
    ]

    def needed(sort_memory: str, gpu_bytes: int = gpu) -> tuple[int, int]:
        estimate = estimate_mapping_memory(
            ref,
            tool,
            threads=threads,
            alignment_threads=str(
                stage_thread_plan(job.threads, metal=bool(gpu_bytes)).alignment
            ),
            memory=sort_memory,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            gpu_bytes=gpu_bytes,
            dense_sa_bytes=dense_sa_bytes,
        )
        physical = estimate.total + additional_ram_bytes
        committed = physical
        if commit is not None and gpu_bytes:
            # WDDM charges the device index and CUDA scratch to host commit
            # (#1119: 10.9 GiB committed vs 5.8 GiB resident at 5M pairs).
            # Allow another 512 MiB for scratch and any implicit dense SA copy.
            committed += estimate.index_resident + max(gpu_bytes - 256 * 1024**2, 0)
        return physical, committed

    ram_fitting = fitting = None
    for candidate in candidates:
        physical, committed = needed(candidate)
        if physical > budget:
            continue
        if ram_fitting is None:
            ram_fitting = candidate
        if commit is None or committed <= commit:
            fitting = candidate
            break
    preferred_memory = memory if job.memory_explicit or mib <= 256 else "256M"
    if (
        fitting is None
        and not windows_cuda
        and native_gpu_preference(job.prefer_native_gpu)
    ):
        logging.info(
            "Preferring %s's native GPU (--prefer-native-gpu): estimated %.1f GiB "
            "against a %.1f GiB memory budget after GPU sort step-down; using %s. "
            "This speed preference may cause heavy swapping.",
            native.binary,
            needed(preferred_memory)[0] / 1024**3,
            budget / 1024**3,
            preferred_memory,
        )
        return AlignerCommand(
            tool,
            native,
            gpu=True,
            dense_sa=dense_sa,
            memory_decision=NativeMemoryDecision(
                "prefer_gpu",
                needed(preferred_memory)[0],
                budget,
                memory,
                preferred_memory,
            ),
        ), preferred_memory

    if fitting is None:
        requested_memory = memory
        gpu_estimate, gpu_commit = needed(candidates[-1])
        staged_estimate = None
        if native.gpu == "metal" and samblaster:
            chunk_memory = (
                memory if job.memory_explicit or settings.get("memory_limit") else "2G"
            )
            chunk_amount, chunk_unit = parse_memory(chunk_memory)
            if chunk_amount * (1024 if chunk_unit == "G" else 1) > 2048:
                chunk_memory = "2G"
            chunk_amount, chunk_unit = parse_memory(chunk_memory)
            chunk_mib = chunk_amount * (1024 if chunk_unit == "G" else 1)
            chunk_candidates = [chunk_memory] + [
                f"{step}M" for step in (1024, 512, 256) if step < chunk_mib
            ]
            for chunk_memory in chunk_candidates:
                staged_estimate = (
                    estimate_mapping_memory(
                        ref,
                        tool,
                        threads=threads,
                        alignment_threads=str(
                            stage_thread_plan(job.threads, metal=True).alignment
                        ),
                        memory=chunk_memory,
                        samblaster=True,
                        reads=reads,
                        platform=job.platform,
                        read_length=read_length,
                        gpu_bytes=gpu,
                        dense_sa_bytes=dense_sa_bytes,
                        staged=True,
                    ).total
                    + additional_ram_bytes
                )
                if staged_estimate <= budget:
                    break
            assert staged_estimate is not None
            if staged_estimate <= budget:
                logging.info(
                    "Separating Metal alignment from duplicate marking/sorting: "
                    "estimated %.1f GiB against %.1f GiB; compressed staged checkpoints.",
                    staged_estimate / 1024**3,
                    budget / 1024**3,
                )
                return AlignerCommand(
                    tool,
                    native,
                    gpu=True,
                    dense_sa=dense_sa,
                    staged=True,
                    memory_decision=NativeMemoryDecision(
                        "memory_budget_metal_staged",
                        staged_estimate,
                        budget,
                        memory,
                        chunk_memory,
                        staged_estimate,
                    ),
                ), chunk_memory
        reason = "memory_budget_cpu"
        limit = budget
        if commit is not None and gpu_commit > commit:
            reason = "windows_commit_budget_cpu"
            gpu_estimate, limit = gpu_commit, commit
            logging.info(
                "Running %s on the CPU: CUDA needs about %.1f GiB of Windows commit "
                "but only %.1f GiB is available (RAM plus page file).",
                native.binary,
                gpu_commit / 1024**3,
                commit / 1024**3,
            )
        else:
            override = (
                ""
                if windows_cuda
                else " Use --prefer-native-gpu (config prefer_native_gpu=true) "
                "to favor speed instead, accepting possible heavy swapping."
            )
            logging.info(
                "Running %s on the CPU: with its %s GPU backend the alignment "
                "is estimated to need %.1f GiB, more than the %.1f GiB memory budget.%s",
                native.binary,
                _gpu_name(native),
                gpu_estimate / 1024**3,
                budget / 1024**3,
                override,
            )
        cpu_budget = min(budget, commit) if commit is not None else budget
        if mib > 256 and needed(memory, 0)[0] > cpu_budget:
            logging.info(
                "Sorting with 256M per thread (not %s) to reduce the over-budget "
                "CPU pipeline's concurrent memory; workers and batch size are unchanged.",
                memory,
            )
            memory = "256M"
        aligner = AlignerCommand(
            tool,
            native,
            dense_sa=dense_sa,
            memory_decision=NativeMemoryDecision(
                reason,
                gpu_estimate,
                limit,
                requested_memory,
                memory,
                staged_estimate,
            ),
        )
    else:
        commit_limited = fitting != ram_fitting
        if fitting != memory:
            logging.info(
                f"Sorting with {fitting} per thread (not {memory}) to leave "
                f"{'Windows commit ' if commit_limited else ''}room for "
                f"{native.binary}'s {_gpu_name(native)} GPU backend; the output "
                "is the same."
            )
        decision = (
            NativeMemoryDecision(
                "windows_commit_budget_low_sort"
                if commit_limited
                else f"memory_budget_{native.gpu}_low_sort",
                needed(fitting)[1 if commit_limited else 0],
                commit if commit_limited and commit is not None else budget,
                memory,
                fitting,
            )
            if fitting != memory
            else None
        )
        aligner = AlignerCommand(
            tool, native, gpu=True, dense_sa=dense_sa, memory_decision=decision
        )
        memory = fitting
    return aligner, memory


def _gpu_name(native: NativeAligner) -> str:
    return "CUDA" if native.gpu == "cuda" else "Metal"


def _gpu_budget(additional_ram_bytes: int) -> int:
    """The RAM an aligner's GPU backend may count on: this machine's less
    headroom, and for streaming realign (additional_ram_bytes) no more than is
    available now."""
    budget = psutil.virtual_memory().total - _GPU_HEADROOM_BYTES
    if additional_ram_bytes:
        budget = min(budget, available_memory())
    return int(budget)


def bwa_mem3_build(job: AlignJob, tool: str, ref: str) -> NativeAligner | None:
    """The bwa-mem3 (wgse-bwa-mem3 on Apple Silicon and Linux x86-64, else
    Bioconda's on Linux: find_bwa_mem3) that can write ``tool``'s exact
    records (--compat) for this run, memory aside, or None. It needs the reference's bwa-mem2 index.
    Ultima reads keep BWA, which is qualified for their flow tags."""
    if tool not in BWA_MEM3_COMPAT or job.platform == "ultima":
        return None
    if not bwa_mem2.has_bwa_mem3_index(ref):
        return None
    return find_bwa_mem3(job.native_aligners, instead=tool)


def bwa_mem3_stand_in(
    job: AlignJob,
    tool: str,
    ref: str,
    *,
    threads: str,
    memory: str,
    samblaster: bool,
    reads: int | None,
    read_length: float | None,
    additional_ram_bytes: int = 0,
) -> AlignerCommand | None:
    """bwa-mem3 writing bwa's or bwa-mem2's exact records (--compat) in
    place of ``tool`` (bwa_mem3_build), or None when its pipeline won't fit.

    In bwa's place it needs more memory than bwa, so its estimate must fit
    the RAM available now; otherwise (on small Macs) wgse-bwa or bwa aligns.
    In bwa-mem2's place it is taken when it needs no more than the bwa-mem2
    that would run instead (wgse-bwa-mem2 0.5.0 and later without the .0123
    file), or fits this Mac's RAM less headroom (as wgse-bwa's GPU backend must);
    an explicit --aligner bwa-mem2 then still stops when it doesn't fit the
    RAM available (_bwa_setup's check_mapping_memory). When auto chose
    bwa-mem2 it must fit the RAM available, as auto's choice did."""
    native = bwa_mem3_build(job, tool, ref)
    if native is None:
        return None
    aligner = AlignerCommand(
        tool,
        native,
        gpu=native.gpu is not None,
        options=(f"--compat={BWA_MEM3_COMPAT[tool]}",),
    )

    def needed(engine: str, gpu_bytes: int = 0) -> int:
        estimate = estimate_mapping_memory(
            ref,
            engine,
            threads=threads,
            alignment_threads=str(
                stage_thread_plan(job.threads, metal=bool(gpu_bytes)).alignment
            ),
            memory=memory,
            samblaster=samblaster,
            reads=reads,
            platform=job.platform,
            read_length=read_length,
            gpu_bytes=gpu_bytes,
        )
        return estimate.total + additional_ram_bytes

    stand_in = needed("bwa-mem3", aligner_gpu_bytes(aligner, ref, reads))
    budget = _gpu_budget(additional_ram_bytes)
    if tool == "bwa":
        limit = min(budget, available_memory())
        room = f"the {limit / 1024**3:.1f} GiB of RAM available"
    elif requested_aligner(job) == "auto":
        # Auto chose bwa-mem2 because it fits the RAM available now.
        limit = available_memory()
        room = f"the {limit / 1024**3:.1f} GiB of RAM available"
    else:
        limit = max(budget, needed(bwa_mem2_memory_model(job.native_aligners)))
        room = f"the {budget / 1024**3:.1f} GiB of RAM it can use"
    if stand_in > limit:
        logging.info(
            f"Not using {native.binary} in place of {tool}: the alignment is "
            f"estimated to need {stand_in / 1024**3:.1f} GiB with it, more than "
            f"{room}."
        )
        return None
    return aligner


def bwa_mem2_memory_model(native_aligners: str | None) -> str:
    """The memory model of bwa-mem2 for a run with this native_aligners
    setting: its Apple Silicon build's when that maps without the .0123 file
    (AlignerCommand.memory_model)."""
    native = find_native_aligner("bwa-mem2", native_aligners, probe=True)
    return AlignerCommand("bwa-mem2", native).memory_model


def bwa_mem2_index_ready(model: str, ref: str) -> bool:
    """Whether ``ref`` has every index file that the aligner of memory
    ``model`` (AlignerCommand.memory_model) reads: bwa-mem3 and wgse-bwa-mem2
    0.5.0 and later don't read the .0123 file."""
    if model in {"bwa-mem3", "wgse-bwa-mem2"}:
        return bwa_mem2.has_bwa_mem3_index(ref)
    return bwa_mem2.has_index(ref)


def bwa_index_suffixes(model: str) -> tuple[str, ...]:
    """Sidecars read by the selected BWA build, including optional ALT data."""
    if model in {"bwa-mem3", "wgse-bwa-mem2"}:
        return (*bwa_mem2.BWA_MEM3_INDEX_SUFFIXES, ".alt")
    if model == "bwa-mem2":
        return (*bwa_mem2.INDEX_SUFFIXES, ".alt")
    return (*bwa_index.INDEX_SUFFIXES, ".alt")


def aligner_gpu_bytes(aligner: AlignerCommand, ref: str, reads: int | None) -> int:
    """What the aligner's GPU backend (Metal or CUDA) needs beyond its CPU estimate."""
    if not aligner.gpu:
        return 0
    if aligner.engine == "bwa":
        return bwa_gpu_memory(ref, reads, dense_sa_file=aligner.dense_sa is not None)
    if aligner.engine == "bwa-mem3":
        cuda = aligner.native is not None and aligner.native.gpu == "cuda"
        return BWA_MEM3_CUDA_BYTES if cuda else BWA_MEM3_METAL_BYTES
    if aligner.engine == "minimap2":
        return MINIMAP2_CUDA_BYTES
    return 0


def bwa_mem3_command(job: AlignJob) -> AlignerCommand:
    """How to run --aligner bwa-mem3: wgse-bwa-mem3 (on Apple Silicon and
    Linux x86-64), else bwa-mem3 from PATH or its Pixi environment, writing
    bwa's records (--compat=bwa-mem) or, with --fast, its faster preset's."""
    options = (BWA_MEM3_FAST,) if job.fast else ("--compat=bwa-mem",)
    native = find_native_aligner("bwa-mem3", job.native_aligners)
    if native is not None:
        return AlignerCommand(
            "bwa-mem3", native, gpu=native.gpu is not None, options=options
        )
    failure = None
    if get_tool_path("bwa-mem3") is None or (failure := bwa_mem3_failure()):
        option = "--aligner bwa-mem3" if job.aligner == "bwa-mem3" else "--fast"
        otherwise = (
            "drop --fast"
            if option == "--fast"
            else "align with --aligner auto" + (" without --fast" if job.fast else "")
        )
        if failure:
            # #727: a build that crashes at startup would otherwise fail the
            # alignment pipeline with a bare signal.
            raise WGSExtractError(
                f"{option} needs bwa-mem3, and the installed one can't run: "
                f"{failure}. Install a bwa-mem3 that runs here, or {otherwise}."
            )
        if (
            native_setting(job.native_aligners) == "off"
            and "bwa-mem3" in platform_binaries()
            and shutil.which("wgse-bwa-mem3")
        ):
            raise WGSExtractError(
                f"{option} needs bwa-mem3: wgse-bwa-mem3 is installed, but "
                "native_aligners is off (use --native-aligners auto), and "
                f"bwa-mem3 isn't installed. Or {otherwise}."
            )
        install = pixi_install_command("bwa-mem3") or (
            "pixi install --locked -e bwa-mem3"
        )
        raise WGSExtractError(
            f"{option} needs wgse-bwa-mem3 (Apple Silicon, Linux x86-64) or bwa-mem3 "
            f"(Bioconda: `{install}`); for "
            f"wgse-bwa-mem3, {INSTALL_HINT}. Or {otherwise}."
        )
    return AlignerCommand("bwa-mem3", options=options)


def require_bwa_mem3_index(ref: str) -> None:
    """bwa-mem3 maps with bwa-mem2's index, which it doesn't build here."""
    if not bwa_mem2.has_bwa_mem3_index(ref):
        missing = ", ".join(
            s for s in bwa_mem2.BWA_MEM3_INDEX_SUFFIXES if not Path(ref + s).is_file()
        )
        raise UsageError(
            f"bwa-mem3 maps with bwa-mem2's index, and {ref} has none "
            f"({missing or 'its build is incomplete'}). Make it with "
            f"`wgsextract ref index --aligner bwa-mem2 --ref {ref}` (or "
            f"`wgse-bwa-mem3 index {ref}`), or align with --aligner bwa."
        )
