from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

from wgsextract_cli.core.runtime import (
    ThreadTuningProfile,
    default_thread_tuning_profile,
)


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


def parse_thread_count(value: object) -> int | None:
    """Parse a --threads / cpu_threads value; None means "auto"."""
    if value is None or (isinstance(value, str) and value.strip().lower() == "auto"):
        return None
    try:
        count = int(str(value).strip())
    except ValueError:
        count = 0
    if count < 1:
        raise ValueError(f"expected a positive integer or 'auto', got {value!r}")
    return count


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
    if threads_arg is not None and str(threads_arg).strip().lower() != "auto":
        threads_arg = max(1, int(threads_arg))
    plan = stage_thread_plan(threads_arg, thread_profile_factory=thread_profile_factory)
    plan.record()
    threads_count = getattr(plan, stage)
    threads = str(threads_count)

    if memory_arg is not None and str(memory_arg).strip().lower() != "auto":
        memory = str(memory_arg)
    else:
        from wgsextract_cli.core.alignment.memory import total_memory
        from wgsextract_cli.core.utils import WGSExtractError

        per_thread_mib = total_memory() // 4 // threads_count // (1024**2)
        if per_thread_mib < 1:
            raise WGSExtractError(
                "Not enough RAM for 1 MiB per thread in the automatic memory budget; "
                "lower --threads."
            )
        memory = (
            f"{per_thread_mib // 1024}G"
            if per_thread_mib >= 1024
            else f"{per_thread_mib}M"
        )

    return threads, memory
