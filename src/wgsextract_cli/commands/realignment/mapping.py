"""Resumable alignment and index publication."""

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from wgsextract_cli.core.native_aligners import native_gpu_preference
from wgsextract_cli.core.read_counts import (
    fastq_input_statistics,
)
from wgsextract_cli.core.results import current_result
from wgsextract_cli.core.samtools_commands import (
    regular_files_bytes,
    sort_memory,
)
from wgsextract_cli.core.steps import Decision, Step, StepJournal
from wgsextract_cli.core.utils import (
    get_resource_defaults,
)
from wgsextract_cli.core.warnings import indexed_read_count

from .. import align as align_cmd
from .preparation import (
    RealignJob,
)
from .resources import (
    _short_read_plan,
)


def _align_tools(aligner: str, platform: str | None, gpu: bool) -> list[str]:
    """The tools whose versions shape an alignment (bwa may become bwa-mem2)."""
    if gpu:
        return ["samtools", "pbrun"]
    tools = {
        "bwa": ["bwa", "bwa-mem2", "samblaster"],
        "bwa-mem2": ["bwa-mem2", "samblaster"],
        "bwa-mem3": ["bwa-mem3", "samblaster"],
        "minimap2": ["minimap2"],
        "pbmm2": ["pbmm2"],
    }.get(aligner, [aligner])
    return ["samtools", *tools, *(["gatk"] if platform == "ultima" else [])]


def _align_step(
    job: RealignJob,
    aligner: str,
    target: str | None,
    out_path: str,
    reads: dict[str, str | None],
) -> Step:
    """The alignment step: map, mark duplicates and sort into out_path.
    Its reads are the input BAM/CRAM, also when they reach the aligner through
    extracted FASTQs (_run_steps reruns it whenever extraction reruns)."""
    reference = target if target and Path(target).is_file() else None
    return Step(
        "align",
        inputs={
            **reads,
            "reference": reference,
            "input_reference": job.input_ref,
        },
        outputs=[out_path],
        params={
            "aligner": aligner,
            "platform": job.platform,
            "preset": job.preset,
            "long_read": bool(job.long_read),
            "format": job.format,
            **(
                {"output_profile": job.output_profile}
                if job.output_profile not in (None, "default")
                else {}
            ),
            "sample": job.sample,
            "gpu": bool(job.gpu),
            "ref": None if reference else job.ref,
            # Only when set, so earlier runs' journals still match.
            **({"fast": True} if job.fast else {}),
            **(
                {"prefer_native_gpu": True}
                if aligner in align_cmd.BWA_ALIGNERS
                and _native_gpu_preference_changes_plan(job, target, reads)
                else {}
            ),
        },
        tools=_align_tools(aligner, job.platform, bool(job.gpu)),
        # 2: a CRAM's chunked realign could drop unplaced reads (#655).
        version=2 if aligner == "minimap2" and _is_cram(job.input) else 1,
    )


def _native_gpu_preference_changes_plan(
    job: RealignJob, target: str | None, reads: dict[str, str | None]
) -> bool:
    """Key the speed preference only when normal native GPU plans cannot fit."""
    if (
        not native_gpu_preference(job.prefer_native_gpu)
        or job.gpu
        or not target
        or not Path(target).is_file()
        or align_cmd.short_read_tool(job) not in align_cmd.BWA_ALIGNERS
    ):
        return False
    threads, memory = get_resource_defaults(job.threads, job.memory)
    r1, r2 = reads.get("fastq_r1"), reads.get("fastq_r2")
    paths = [path for path in (r1, r2) if path] or [job.input]
    memory = sort_memory(threads, memory, input_bytes=regular_files_bytes(paths))
    statistics = fastq_input_statistics(r1, r2) if r1 else None
    count = statistics.reads if statistics else indexed_read_count(job.input, threads)
    plan, _ = _short_read_plan(
        job,
        target,
        threads=threads,
        memory=memory,
        samblaster=align_cmd.duplicate_marker(paired=bool(r2) or not r1)
        == "samblaster",
        reads=count,
        read_length=statistics.read_length if statistics else None,
    )
    return (
        plan.memory_decision is not None and plan.memory_decision.reason == "prefer_gpu"
    )


def _is_cram(path: str) -> bool:
    return urlsplit(path).path.lower().endswith(".cram")


def _index_path(out_path: str) -> str:
    return out_path + (".crai" if out_path.endswith(".cram") else ".bai")


def _index_step(out_path: str) -> Step:
    return Step(
        "index",
        inputs={"alignment": out_path},
        outputs=[_index_path(out_path)],
        tools=["samtools"],
    )


def _align(
    journal: StepJournal,
    decision: Decision,
    run: Callable[[], None],
    *,
    keep_partial: bool = False,
) -> None:
    """Run the align step. Aligners index their output as they write it, so
    that index is then recorded as the index step. ``keep_partial``: the
    aligner resumes an interrupted attempt's work (a chunked alignment)."""
    out_path = str(decision.step.outputs[0])
    aligning = journal.start(decision, keep_partial=keep_partial)
    run()
    aligning.metrics["aligner"] = current_result().metrics.get("aligner")
    aligning.metrics.update(_native_aligner_metrics(current_result().metrics))
    journal.finish(aligning)
    index = journal.check(_index_step(out_path))
    journal.finish(journal.start(index, keep_partial=True))


def _resume_alignment(
    journal: StepJournal,
    decision: Decision,
    out_path: str,
    target: str | None,
    threads: str,
) -> None:
    """Finish from a completed align step: index the output unless the index
    step is complete, then report the alignment as this run's output."""
    indexing = journal.decide(_index_step(out_path))
    if journal.dry_run:
        return
    if indexing.run:
        with journal.running(indexing):
            logging.info("Indexing %s ...", out_path)
            align_cmd._index_output(out_path, threads=threads)
    aligner = str(decision.metrics.get("aligner") or "aligner")
    align_cmd.record_alignment(out_path, aligner, target or "")
    native = _native_aligner_metrics(decision.metrics)
    current_result().add_metrics(**native)
    if native.get("native_aligner") and native.get("native_aligner_version"):
        # The build that made the alignment, not whichever is installed now.
        current_result().record_tool_version(
            str(native["native_aligner"]), str(native["native_aligner_version"])
        )
    if native.get("bwa_mem3_mode") == "fast":
        align_cmd.warn_bwa_mem3_fast(str(native.get("native_aligner") or "bwa-mem3"))


def _native_aligner_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """The metrics naming the Apple Silicon aligner build that made an
    alignment, and bwa-mem3's output mode (AlignerCommand.record), kept in
    the journal for a resume, including the native_aligner-prefixed memory
    decision, estimate/budget bytes and requested/actual sort memory."""
    return {
        key: value
        for key, value in metrics.items()
        if key.startswith("native_aligner") or key == "bwa_mem3_mode"
    }
