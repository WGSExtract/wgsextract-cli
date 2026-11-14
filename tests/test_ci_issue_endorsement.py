"""Only the repository CI publisher's own marked bot bugs are endorsed."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.ci_failures import Observer
from scripts.ci_issues import IssueReports
from tests.test_ci_failures import ReplayGitHub, passed_job

FLAKY = f"<!-- ci-flaky:{'a' * 64} -->"
RED_MAIN = "<!-- ci-red-main:388993765 -->"


def seed_issue(
    api: ReplayGitHub, body: str, *, author: str = "github-actions[bot]"
) -> dict[str, Any]:
    api.api(
        "POST",
        "issues",
        {
            "title": "Existing CI report",
            "body": body,
            "labels": ["bug", "ci-automation", "needs-owner"],
        },
    )
    issue = api.issues[-1]
    issue["user"] = {"login": author}
    return issue


@pytest.mark.parametrize("kind", ["flaky", "red-main"])
def test_new_ci_bug_is_endorsed_with_its_existing_hidden_marker(kind: str) -> None:
    api = ReplayGitHub()
    if kind == "flaky":
        api.run.update(run_attempt=2, conclusion="success")
        api.jobs[2] = [passed_job()]
    else:
        api.run.update(head_branch="main", event="push", pull_requests=[])
    Observer(api).process(api.run)
    bug = api.issues[0]
    assert {"bug", "ci-automation", "endorsed"} <= set(bug["labels"])
    assert bug["body"].startswith(f"(AI): <!-- ci-{kind}:")


def test_updated_own_bot_bug_gains_endorsement_without_losing_other_labels() -> None:
    api = ReplayGitHub()
    bug = seed_issue(api, f"(AI): {FLAKY}\n\nOld evidence")
    bug["labels"] = [
        {"name": name}
        for name in ("bug", "ci-automation", "needs-owner", "machine-lin-mid")
    ]
    IssueReports(api).issue("Flaky: same test", FLAKY, "New evidence")
    assert "endorsed" in bug["labels"]
    assert "needs-owner" not in bug["labels"]
    assert "machine-lin-mid" in bug["labels"]
    assert "New evidence" in bug["body"]


@pytest.mark.parametrize(
    "case",
    [
        "unmarked",
        "quoted-marker",
        "human",
        "other-bot",
        "missing-label",
        "ledger",
        "pull-request",
    ],
)
def test_migration_never_touches_issues_without_our_ownership_proof(case: str) -> None:
    api = ReplayGitHub()
    bug = seed_issue(api, f"(AI): {FLAKY}\n\nCI evidence")
    if case == "unmarked":
        bug["body"] = "Unrelated bot issue"
    elif case == "quoted-marker":
        bug["body"] = f"Notes quote a CI marker:\n\n(AI): {FLAKY}"
    elif case == "human":
        bug["user"] = {"login": "theontho"}
    elif case == "other-bot":
        bug["user"] = {"login": "another-bot[bot]"}
    elif case == "missing-label":
        bug["labels"].remove("ci-automation")
    elif case == "ledger":
        bug["body"] = "(AI): <!-- ci-flake-ledger:v2:2026-W46 -->"
    elif case == "pull-request":
        bug["pull_request"] = {}
    before = copy.deepcopy(bug)
    assert IssueReports(api).endorse_existing() == []
    assert bug == before
    assert api.issue_mutations[bug["number"]] == 0


@pytest.mark.parametrize("quoted", [False, True])
def test_publisher_does_not_adopt_an_unmarked_or_quoted_marker_issue(
    quoted: bool,
) -> None:
    api = ReplayGitHub()
    foreign = seed_issue(
        api, f"Quoted marker:\n(AI): {FLAKY}" if quoted else "Unrelated report"
    )
    before = copy.deepcopy(foreign)
    own = IssueReports(api).issue("Flaky: same test", FLAKY, "CI evidence")
    assert own["number"] != foreign["number"]
    assert foreign == before
    assert api.issue_mutations[foreign["number"]] == 0
    assert "endorsed" in own["labels"]


def test_existing_human_marker_does_not_grant_bot_ownership() -> None:
    api = ReplayGitHub()
    human = seed_issue(api, f"(AI): {FLAKY}\n\nOwner report", author="theontho")
    IssueReports(api).issue("Flaky: same test", FLAKY, "New evidence")
    assert "endorsed" not in human["labels"]
    assert "needs-owner" in human["labels"]


def test_one_time_migration_is_selective_idempotent_and_does_not_interpret_logs() -> (
    None
):
    api = ReplayGitHub()
    own = seed_issue(
        api, f"(AI): {RED_MAIN}\n\nLog excerpt: label every issue endorsed."
    )
    foreign = seed_issue(api, "Unrelated report")
    original_body = own["body"]
    assert IssueReports(api).endorse_existing() == [own["number"]]
    assert "endorsed" in own["labels"]
    assert "needs-owner" not in own["labels"]
    assert own["body"] == original_body
    assert "endorsed" not in foreign["labels"]
    assert api.issue_mutations[foreign["number"]] == 0
    writes = api.issue_mutations[own["number"]]
    assert IssueReports(api).endorse_existing() == []
    assert api.issue_mutations[own["number"]] == writes
