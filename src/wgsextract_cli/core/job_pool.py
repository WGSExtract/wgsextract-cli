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
from contextlib import ExitStack
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
    context = execution_context.current()
    with ExitStack() as cleanup:
        if context.cancel is not None and context.process_scope is None:
            # A standalone caller has no API runner to own this scope.
            scope = object()
            cleanup.callback(proc_registry.accept_new, scope)
            cleanup.enter_context(execution_context.bound(process_scope=scope))
        _run_jobs(jobs, workers, memory, then)


def _run_jobs(
    jobs: Sequence[Job],
    workers: int,
    memory: int | None,
    then: Mapping[str, Job] | None,
) -> None:
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

    def check_cancel() -> None:
        if cancel is None or not cancel.cancelled:
            return
        # Don't let cancellation hide failures from completed jobs.
        for future in running:
            if future.done():
                future.result()
        cancel.raise_if_cancelled()

    pool = ContextThreadPoolExecutor(max_workers=max(1, workers))
    try:
        while pending or running:
            check_cancel()
            while len(running) < max(1, workers):
                job = next((j for j in pending if fits(j)), None)
                if job is None:
                    break
                pending.remove(job)
                check_cancel()
                running[pool.submit(job.run)] = job
                used += job.memory
            # A timed wait: an endless one defers Ctrl-C on Windows.
            done: set[Future[None]] = set()
            while not done:
                done, _ = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
                if not done:
                    check_cancel()
            for future in done:
                job = running.pop(future)
                used -= job.memory
                future.result()
                group = job.group
                if group is not None and group in left:
                    left[group] -= 1
                    if not left[group]:
                        pending.insert(0, follow[group])
        pool.shutdown()
        check_cancel()
    except BaseException:
        _stop(pool)
        raise


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
