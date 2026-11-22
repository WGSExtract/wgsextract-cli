"""Run independent jobs side by side within a CPU and memory budget.

``run_jobs`` starts each job when a worker is free and the jobs already
running, with this one, are estimated to fit the memory budget; one job
always runs, however large. Jobs start in the order given, so callers put
the largest first, and a smaller one may start where a larger one does not
fit. When every job of a group has finished, the group's follow-up job (for
example, joining its parts) starts ahead of the rest.

The first failure stops the run: the operation's running tools are
terminated, nothing more starts, and the failure is raised. Ctrl-C and
cancellation stop it the same way.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass

from wgsextract_cli.core import execution_context
from wgsextract_cli.core.execution_context import ContextThreadPoolExecutor
from wgsextract_cli.core.process_registry import proc_registry


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[], None]
    memory: int = 0  # estimated peak bytes
    group: str | None = None


def run_jobs(
    jobs: Sequence[Job],
    *,
    workers: int,
    memory: int | None = None,
    then: Mapping[str, Job] | None = None,
) -> None:
    """Run ``jobs`` at most ``workers`` at a time within ``memory`` bytes
    (None: unbounded); ``then[group]`` runs once all of the group's jobs
    have finished."""
    pending = list(jobs)
    follow = dict(then or {})
    left = Counter(
        job.group for job in jobs if job.group is not None and job.group in follow
    )
    running: dict[Future[None], Job] = {}
    used = 0

    def fits(job: Job) -> bool:
        return not running or memory is None or used + job.memory <= memory

    cancel = execution_context.current().cancel
    pool = ContextThreadPoolExecutor(max_workers=max(1, workers))
    try:
        while pending or running:
            if cancel is not None:
                cancel.raise_if_cancelled()
            while len(running) < max(1, workers):
                job = next((j for j in pending if fits(j)), None)
                if job is None:
                    break
                pending.remove(job)
                running[pool.submit(job.run)] = job
                used += job.memory
            # A timed wait: an endless one defers Ctrl-C on Windows.
            done: set[Future[None]] = set()
            while not done:
                done, _ = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in done:
                job = running.pop(future)
                used -= job.memory
                future.result()
                group = job.group
                if group is not None and group in left:
                    left[group] -= 1
                    if not left[group]:
                        pending.insert(0, follow[group])
    except BaseException:
        _stop(pool)
        raise
    pool.shutdown()
    if cancel is not None:
        cancel.raise_if_cancelled()


def _stop(pool: ContextThreadPoolExecutor) -> None:
    """Terminate this operation's running tools, refusing new ones until the
    workers have returned."""
    context = execution_context.current()
    scope = context.process_scope
    proc_registry.cancel(scope)
    try:
        pool.shutdown(wait=True, cancel_futures=True)
    finally:
        # A cancelled operation keeps refusing until its runner accepts again;
        # without a scope there is no runner to do so.
        if scope is None or context.cancel is None or not context.cancel.cancelled:
            proc_registry.accept_new(scope)
