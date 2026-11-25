from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

from wgsextract_cli.core.errors import WGSExtractError
from wgsextract_cli.core.runtime import (
    ThreadTuningProfile,
    default_thread_tuning_profile,
)
from wgsextract_cli.core.thread_count import parse_thread_count


@dataclass(frozen=True)
class StageThreadPlan:
    alignment: int
    sort: int
    markdup: int
    compression: int
    calling: int

    def record(self) -> None:
        from wgsextract_cli.core.results import current_result

        current_result().provenance.params["thread_plan"] = asdict(self)


def stage_thread_plan(
    threads_arg: int | str | None = None,
    *,
    metal: bool = False,
    thread_profile_factory: Callable[
        [], ThreadTuningProfile
    ] = default_thread_tuning_profile,
) -> StageThreadPlan:
    """An explicit count applies everywhere; auto follows the host's core levels."""
    count = parse_thread_count(threads_arg)
    if count is not None:
        return StageThreadPlan(count, count, count, count, count)
    profile = thread_profile_factory()
    count = profile.threads
    total = getattr(profile, "cpu_alignment_threads", None) or count
    alignment = count if metal else total
    return StageThreadPlan(alignment, count, count, total, total)


def get_resource_threads(
    threads_arg: int | str | None = None,
    thread_profile_factory: Callable[
        [], ThreadTuningProfile
    ] = default_thread_tuning_profile,
    *,
    stage: Literal["sort", "markdup", "compression", "calling"] = "sort",
) -> str:
    """Resolve and record the thread plan without budgeting sort buffers."""
    if threads_arg is not None and str(threads_arg).strip().lower() != "auto":
        threads_arg = max(1, int(threads_arg))
    plan = stage_thread_plan(threads_arg, thread_profile_factory=thread_profile_factory)
    plan.record()
    return str(getattr(plan, stage))


def get_resource_defaults(
    threads_arg: int | str | None = None,
    memory_arg: str | None = None,
    thread_profile_factory: Callable[
        [], ThreadTuningProfile
    ] = default_thread_tuning_profile,
    *,
    stage: Literal["sort", "markdup", "compression", "calling"] = "sort",
) -> tuple[str, str]:
    """
    Calculate default CPU threads and memory if not provided.
    Memory defaults to at most 25% of effective RAM split across threads.
    """
    threads = get_resource_threads(threads_arg, thread_profile_factory, stage=stage)

    if memory_arg is not None and str(memory_arg).strip().lower() != "auto":
        memory = str(memory_arg)
    else:
        from wgsextract_cli.core.alignment.memory import total_memory

        per_thread_bytes = total_memory() // 4 // int(threads)
        if per_thread_bytes < 1:
            raise WGSExtractError(
                "Not enough RAM for the automatic memory budget; lower --threads."
            )
        if per_thread_bytes >= 1024**3:
            memory = f"{per_thread_bytes // 1024**3}G"
        elif per_thread_bytes >= 1024**2:
            memory = f"{per_thread_bytes // 1024**2}M"
        else:
            # Collation and JVM callers use the aggregate, without samtools' floor.
            mib = f"{per_thread_bytes / 1024**2:.20f}".rstrip("0")
            memory = f"{mib}M"

    return threads, memory
