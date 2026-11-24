"""Ready admission control reads Actions state (#1373, merge-queue starvation)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts import ci_capacity

REPO = "WGSExtract/wgsextract-cli"


def run(
    run_id: int, event: str, title: str = "x", path: str = "", age_minutes: int = 1
) -> dict[str, Any]:
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return {
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "id": run_id,
        "event": event,
        "path": path or ".github/workflows/ubuntu_ci.yml",
        "display_title": title,
        "pull_requests": [{"number": run_id}] if event == "pull_request" else [],
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def area(run_id: int, state: str = "ready", age_minutes: int = 1) -> dict[str, Any]:
    return run(
        run_id,
        "pull_request",
        f"PR area checks / synchronize / {state}",
        ".github/workflows/pr_ci.yml",
        age_minutes,
    )


def jobs(*statuses: str) -> list[dict[str, Any]]:
    return [{"name": f"job{i}", "status": s} for i, s in enumerate(statuses)]


class FakeActions:
    """The gh api boundary: one canned run/job listing per poll."""

    def __init__(self, *snapshots: dict[str, Any]) -> None:
        self.snapshots = snapshots
        self.polls = 0
        self.calls: list[str] = []

    def __call__(self, path: str, *args: str) -> Any:
        self.calls.append(path)
        if path == "graphql":
            current = self.snapshots[min(self.polls, len(self.snapshots) - 1)]
            nodes = [pr for pr in current["pulls"] if "priority" in pr["labels"]]
            pulls = {"nodes": nodes, "pageInfo": {"hasNextPage": False}}
            return {"data": {"repository": {"pullRequests": pulls}}}
        if "status=queued" in path:
            self.polls += 1
        current = self.snapshots[min(self.polls, len(self.snapshots)) - 1]
        if "/jobs?" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return {"jobs": current["jobs"].get(run_id, [])}
        status = path.split("status=")[1].split("&")[0]
        return {"workflow_runs": [r for r in current["runs"] if r["status"] == status]}


def snapshot(
    *entries: tuple[dict[str, Any], str, list[dict[str, Any]]],
    pulls: tuple[dict[str, Any], ...] = (),
) -> Any:
    return {
        "runs": [{**r, "status": status} for r, status, _ in entries],
        "jobs": {r["id"]: job_list for r, _, job_list in entries},
        "pulls": pulls,
    }


def priority_pr(
    number: int,
    *,
    draft: bool = False,
    queued: bool = False,
    head_repository: str = REPO,
    labels: tuple[str, ...] = ("priority",),
) -> dict[str, Any]:
    return {
        "number": number,
        "labels": labels,
        "isDraft": draft,
        "headRefName": "contributor/topic",
        "headRefOid": "a" * 40,
        "headRepository": {"nameWithOwner": head_repository},
        "mergeQueueEntry": {"id": "entry"} if queued else None,
    }


def invoke(
    fake: FakeActions, *args: str, capsys: Any
) -> tuple[int, dict[str, Any], str]:
    code = ci_capacity.main(["--repo", REPO, "--json", *args], api=fake)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def test_1373_holds_ready_while_merge_group_job_waits_for_runner(capsys: Any) -> None:
    fake = FakeActions(
        snapshot(
            (
                run(1, "merge_group", "queue"),
                "in_progress",
                jobs("completed", "queued"),
            ),
            (area(2), "in_progress", jobs("in_progress")),
        )
    )
    code, report, _ = invoke(fake, capsys=capsys)
    assert code == ci_capacity.HOLD
    assert report["merge_group_queued"] == 1
    assert report["waiting_for_runner"] == {"merge_group": 1}
    assert [m["prs"] for m in report["ready_matrices"]] == [[2]]
    assert any("merge_group" in reason for reason in report["reasons"])
    assert all(
        call == "graphql" or call.startswith(f"repos/{REPO}/actions/")
        for call in fake.calls
    )


def test_1373_counts_only_active_non_draft_area_runs_against_max_ready(
    capsys: Any,
) -> None:
    entries = [(area(n), "in_progress", jobs("in_progress")) for n in range(10, 15)]
    entries += [
        (area(20, "draft"), "in_progress", jobs("in_progress")),
        (run(21, "pull_request", "Other PR workflow"), "queued", jobs("queued")),
        # Ghost runs stuck "queued" with no jobs hold no runner.
        (area(22, age_minutes=11 * 24 * 60), "queued", []),
        (run(23, "merge_group", "queue"), "in_progress", jobs("in_progress")),
    ]
    code, report, _ = invoke(FakeActions(snapshot(*entries)), capsys=capsys)
    assert code == ci_capacity.HOLD
    assert [m["run"] for m in report["ready_matrices"]] == [10, 11, 12, 13, 14]
    assert report["merge_group_in_progress"] == 1
    assert report["waiting_for_runner"] == {"pull_request": 1}

    code, report, _ = invoke(
        FakeActions(snapshot(*entries)), "--max-ready", "5", capsys=capsys
    )
    assert code == ci_capacity.OK
    assert report["ok"] is True and report["reasons"] == []


def test_1373_fresh_ready_run_counts_before_its_jobs_appear(capsys: Any) -> None:
    # Review: a just-admitted run lists no jobs yet; it must still use a slot.
    entries = [(area(n), "in_progress", jobs("in_progress")) for n in range(10, 14)]
    entries.append((area(30), "queued", []))
    code, report, _ = invoke(FakeActions(snapshot(*entries)), capsys=capsys)
    assert code == ci_capacity.HOLD
    assert 30 in [m["run"] for m in report["ready_matrices"]]


def test_priority_pr_bypasses_runner_and_matrix_pressure(capsys: Any) -> None:
    fake = FakeActions(
        snapshot(
            (run(2, "merge_group"), "queued", jobs("queued")),
            (area(3), "in_progress", jobs("in_progress")),
            pulls=(priority_pr(1, draft=True),),
        )
    )
    code, report, _ = invoke(fake, "--pr", "1", "--max-ready", "0", capsys=capsys)
    assert code == ci_capacity.OK
    assert report["priority_bypass"] == 1
    assert report["reasons"] == []


@pytest.mark.parametrize(
    ("status", "job_statuses"),
    [("pending", []), ("queued", []), ("in_progress", ["in_progress"])],
)
def test_agent_yields_to_priority_ready_run_below_capacity(
    status: str, job_statuses: list[str], capsys: Any
) -> None:
    fake = FakeActions(
        snapshot((area(1), status, jobs(*job_statuses)), pulls=(priority_pr(1),))
    )
    code, report, _ = invoke(fake, "--pr", "2", capsys=capsys)
    assert code == ci_capacity.HOLD
    assert report["priority_ready"] == [1]
    assert report["merge_group_queued"] == 0


def test_agent_yields_to_priority_fork_run_without_pr_metadata(capsys: Any) -> None:
    fork = "collaborator/wgsextract-cli"
    pull = priority_pr(1, head_repository=fork)
    run_without_pr = {
        **area(7),
        "pull_requests": [],
        "head_repository": {"full_name": fork},
        "head_sha": pull["headRefOid"],
        "head_branch": pull["headRefName"],
    }
    fake = FakeActions(
        snapshot((run_without_pr, "queued", []), pulls=(pull,)),
        snapshot(
            ({**run_without_pr, "head_repository": {"full_name": REPO}}, "queued", []),
            pulls=(pull,),
        ),
    )
    code, report, err = invoke(fake, "--wait", "--interval", "0.01", capsys=capsys)
    assert code == ci_capacity.OK
    assert report["priority_ready"] == []
    assert "priority PR(s) #1" in err


def test_agent_yields_to_priority_merge_entry_even_without_ci(capsys: Any) -> None:
    fake = FakeActions(snapshot(pulls=(priority_pr(1, queued=True),)))
    code, report, _ = invoke(fake, "--pr", "2", capsys=capsys)
    assert code == ci_capacity.HOLD
    assert report["priority_queued"] == [1]


@pytest.mark.parametrize("companion_label", ["hands-off", "land-asap"])
def test_collaborator_action_labels_do_not_change_capacity_admission(
    companion_label: str, capsys: Any
) -> None:
    fake = FakeActions(
        snapshot(
            (area(1), "pending", []),
            (area(2), "queued", jobs("queued")),
            pulls=(
                priority_pr(1, labels=("priority", companion_label)),
                priority_pr(2, labels=(companion_label,)),
            ),
        )
    )
    code, report, _ = invoke(fake, "--pr", "1", capsys=capsys)
    assert code == ci_capacity.OK
    assert report["priority_bypass"] == 1

    code, report, _ = invoke(fake, "--pr", "2", capsys=capsys)
    assert code == ci_capacity.HOLD
    assert report["priority_ready"] == [1]


def test_agent_does_not_yield_to_stale_ready_run_of_redrafted_priority_pr(
    capsys: Any,
) -> None:
    fake = FakeActions(
        snapshot(
            (area(1), "in_progress", jobs("in_progress")),
            (area(2, "draft"), "queued", jobs("queued")),
            pulls=(priority_pr(1, draft=True), priority_pr(2)),
        )
    )
    code, report, _ = invoke(fake, capsys=capsys)
    assert code == ci_capacity.OK
    assert report["priority_ready"] == []


def test_1373_wait_admits_once_the_merge_queue_starts(capsys: Any) -> None:
    busy = snapshot((run(1, "merge_group", "queue"), "queued", jobs("queued")))
    free = snapshot(
        (run(1, "merge_group", "queue"), "in_progress", jobs("in_progress"))
    )
    fake = FakeActions(busy, free)
    code, report, err = invoke(fake, "--wait", "--interval", "0.01", capsys=capsys)
    assert code == ci_capacity.OK
    assert report["merge_group_in_progress"] == 1
    assert fake.polls == 2
    assert "HOLD" in err


def test_1373_wait_polls_last_at_the_deadline_then_holds() -> None:
    now = [0.0]
    polls: list[float] = []
    delays: list[float] = []

    def check() -> ci_capacity.Capacity:
        polls.append(now[0])
        return ci_capacity.Capacity(max_ready=4, merge_group_queued=2)

    def sleep(delay: float) -> None:
        delays.append(delay)
        now[0] += delay

    result = ci_capacity.wait(
        check,
        timeout=1000,
        interval=60,
        max_interval=300,
        sleep=sleep,
        clock=lambda: now[0],
        log=lambda _: None,
    )
    assert not result.ok
    assert delays == [60, 120, 240, 300, 280]
    assert polls[-1] == 1000


def test_1373_api_failure_is_an_error_not_an_admission(capsys: Any) -> None:
    def broken(path: str, *args: str) -> Any:
        raise subprocess.CalledProcessError(1, ["gh", "api", path])

    assert ci_capacity.main(["--repo", REPO], api=broken) == ci_capacity.ERROR
    assert "Cannot read Actions capacity" in capsys.readouterr().err
