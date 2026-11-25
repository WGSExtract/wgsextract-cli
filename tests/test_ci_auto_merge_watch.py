"""Stalled auto-merge watcher (#1281); only the GitHub API boundary is replaced."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts.ci_auto_merge_watch import LABEL, watch
from scripts.ci_github import GitHub

REPO = "WGSExtract/wgsextract-cli"


class FakeGitHub(GitHub):
    """Open PRs, labels and comments as GitHub would keep them."""

    def __init__(self, page_size: int = 100) -> None:
        super().__init__(REPO)
        self.page_size = page_size
        self.repository_labels: set[str] = set()
        self.pulls: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[str]] = {}
        self.timeline: dict[int, list[dict[str, Any]]] = {}
        self.listings = 0
        self.comment_failures = 0
        self.label_failures = 0
        self.create_failures = 0

    def add(
        self,
        number: int,
        *mergeable: str,
        auto_merge: bool = True,
        labels: tuple[str, ...] = (),
        base: str = "main",
        state: str = "OPEN",
    ) -> None:
        self.pulls[number] = {
            "states": list(mergeable),
            "auto_merge": auto_merge,
            "labels": set(labels),
            "base": base,
            "head": f"{number}a",
            "state": state,
        }
        self.repository_labels |= set(labels)

    def labels(self, number: int) -> set[str]:
        return set(self.pulls[number]["labels"])

    def request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> Any:
        assert data is not None or method == "DELETE"
        if path == "graphql":
            assert data is not None
            if "number" in data["variables"]:
                return self.pull_timeline(data["variables"]["number"])
            return self.graphql(data["variables"])
        prefix = f"repos/{REPO}/"
        assert path.startswith(prefix), path
        path = path.removeprefix(prefix)
        if method == "POST" and path == "labels":
            assert data is not None
            if self.create_failures:
                self.create_failures -= 1
                raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")
            if data["name"] in self.repository_labels:
                raise subprocess.CalledProcessError(1, "gh", stderr="already_exists")
            self.repository_labels.add(data["name"])
            return data
        parts = path.split("/")
        number = int(parts[1])
        if method == "POST" and parts[2] == "labels":
            assert data is not None
            if self.label_failures:
                self.label_failures -= 1
                raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")
            # GitHub creates unknown labels when adding them to an issue.
            self.repository_labels |= set(data["labels"])
            self.pulls[number]["labels"] |= set(data["labels"])
            return []
        if method == "DELETE" and parts[2] == "labels":
            if parts[3] not in self.pulls[number]["labels"]:
                raise subprocess.CalledProcessError(1, "gh", stderr="Label not found")
            self.pulls[number]["labels"].remove(parts[3])
            self.timeline.setdefault(number, []).append(
                {"__typename": "UnlabeledEvent", "label": {"name": parts[3]}}
            )
            return []
        if method == "POST" and parts[2] == "comments":
            assert data is not None
            if self.comment_failures:
                self.comment_failures -= 1
                raise subprocess.CalledProcessError(
                    1,
                    ["gh", "api", "--method", method, prefix + path, "--input", "-"],
                    stderr="gh: Resource not accessible by integration (HTTP 403)",
                )
            self.comments.setdefault(number, []).append(data["body"])
            self.timeline.setdefault(number, []).append(
                {
                    "__typename": "IssueComment",
                    "body": data["body"],
                    "author": {"login": "github-actions"},
                }
            )
            return {}
        raise AssertionError(f"Unexpected GitHub call: {method} {path}")

    def pull_timeline(self, number: int) -> dict[str, Any]:
        nodes = self.timeline.get(number, [])[-100:]
        return {
            "data": {"repository": {"pullRequest": {"timelineItems": {"nodes": nodes}}}}
        }

    def graphql(self, variables: dict[str, Any]) -> dict[str, Any]:
        assert (variables["owner"], variables["name"]) == tuple(REPO.split("/"))
        start = int(variables["cursor"] or 0)
        if start == 0:
            self.listings += 1
        numbers = sorted(n for n, p in self.pulls.items() if p["state"] == "OPEN")
        closed = [
            {"number": n, "baseRefName": p["base"]}
            for n, p in sorted(self.pulls.items())
            if p["state"] != "OPEN" and variables["label"] in p["labels"]
        ]
        page = numbers[start : start + self.page_size]
        nodes = []
        for number in page:
            pull = self.pulls[number]
            states = pull["states"]
            nodes.append(
                {
                    "number": number,
                    "mergeable": states[min(self.listings, len(states)) - 1],
                    "baseRefName": pull["base"],
                    "headRefOid": pull["head"],
                    "autoMergeRequest": {"enabledAt": "2026-11-21T00:00:00Z"}
                    if pull["auto_merge"]
                    else None,
                    "labels": {"nodes": [{"name": n} for n in sorted(pull["labels"])]},
                }
            )
        end = start + len(page)
        label = variables["label"]
        return {
            "data": {
                "repository": {
                    "label": {"name": label}
                    if label in self.repository_labels
                    else None,
                    "closed": {"nodes": closed},
                    "pullRequests": {
                        "pageInfo": {
                            "hasNextPage": end < len(numbers),
                            "endCursor": str(end),
                        },
                        "nodes": nodes,
                    },
                }
            }
        }


def no_sleep(seconds: float) -> None:
    raise AssertionError("Mergeability was already known")


def test_1281_conflicting_auto_merge_pr_is_labelled_and_told_once() -> None:
    github = FakeGitHub(page_size=2)
    github.add(1205, "CONFLICTING", base="main")
    github.add(1206, "CONFLICTING", auto_merge=False)
    github.add(1207, "MERGEABLE")
    github.add(1208, "CONFLICTING", base="release-0.9")

    assert watch(github, sleep=no_sleep) == {1205: "flagged", 1208: "flagged"}
    assert watch(github, sleep=no_sleep) == {}

    assert LABEL in github.repository_labels
    assert github.labels(1205) == github.labels(1208) == {LABEL}
    assert github.labels(1206) == github.labels(1207) == set()
    assert len(github.comments[1205]) == 1
    assert "Merge `main` into this branch" in github.comments[1205][0]
    assert "Merge `release-0.9`" in github.comments[1208][0]
    assert not github.comments[1205][0].startswith("(AI)")
    assert set(github.comments) == {1205, 1208}


@pytest.mark.parametrize(
    ("state", "auto_merge"), [("MERGEABLE", True), ("CONFLICTING", False)]
)
def test_1281_label_clears_once_auto_merge_is_no_longer_stuck(
    state: str, auto_merge: bool
) -> None:
    github = FakeGitHub()
    github.add(1205, state, auto_merge=auto_merge, labels=(LABEL, "efficiency"))

    assert watch(github, sleep=no_sleep) == {1205: "cleared"}
    assert github.labels(1205) == {"efficiency"}
    assert github.comments == {}


def test_1281_waits_for_github_to_compute_mergeability_after_a_main_push() -> None:
    github = FakeGitHub()
    github.add(1205, "UNKNOWN", "UNKNOWN", "CONFLICTING")
    github.add(1206, "UNKNOWN", "MERGEABLE", labels=(LABEL,))
    github.add(1207, "UNKNOWN", auto_merge=False)
    waits: list[float] = []

    assert watch(github, delay=15, sleep=waits.append) == {
        1205: "flagged",
        1206: "cleared",
    }
    assert waits == [15, 15]
    assert github.labels(1205) == {LABEL}


def test_1281_still_unknown_mergeability_changes_nothing() -> None:
    github = FakeGitHub()
    github.add(1205, "UNKNOWN", labels=(LABEL,))
    github.add(1206, "UNKNOWN")
    waits: list[float] = []

    assert watch(github, attempts=3, sleep=waits.append) == {
        1205: "unknown",
        1206: "unknown",
    }
    assert len(waits) == 2
    assert github.labels(1205) == {LABEL}
    assert github.comments == {}


def test_1281_graphql_errors_stop_before_any_change() -> None:
    class Failing(FakeGitHub):
        def graphql(self, variables: dict[str, Any]) -> dict[str, Any]:
            return {"data": None, "errors": [{"message": "rate limited"}]}

    github = Failing()
    github.add(1205, "CONFLICTING")

    with pytest.raises(RuntimeError, match="rate limited"):
        watch(github, sleep=no_sleep)
    assert github.labels(1205) == set()


def test_1281_dry_run_reports_without_changing_prs() -> None:
    github = FakeGitHub()
    github.add(1205, "CONFLICTING")
    github.add(1206, "MERGEABLE", labels=(LABEL,))

    assert watch(github, sleep=no_sleep, dry_run=True) == {
        1205: "flagged",
        1206: "cleared",
    }
    assert github.labels(1205) == set() and github.labels(1206) == {LABEL}
    assert github.comments == {}


def test_1281_disabled_auto_merge_clears_label_without_mergeability() -> None:
    github = FakeGitHub()
    github.add(1205, "UNKNOWN", auto_merge=False, labels=(LABEL,))

    assert watch(github, sleep=no_sleep) == {1205: "cleared"}
    assert github.labels(1205) == set()


def test_1281_failed_label_is_retried_without_a_second_comment() -> None:
    github = FakeGitHub()
    github.add(1205, "CONFLICTING")
    github.add(1206, "CONFLICTING")
    github.label_failures = 1

    # One PR's failure doesn't stop the next one.
    assert watch(github, sleep=no_sleep) == {1205: "failed", 1206: "flagged"}
    assert len(github.comments[1205]) == 1 and github.labels(1205) == set()

    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    assert github.labels(1205) == {LABEL}
    assert len(github.comments[1205]) == 1


def test_1546_comment_403_names_endpoint_and_does_not_stop_other_prs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = FakeGitHub()
    github.add(1357, "CONFLICTING")
    github.add(1430, "CONFLICTING")
    github.comment_failures = 1

    assert watch(github, sleep=no_sleep) == {1357: "failed", 1430: "flagged"}
    assert github.labels(1357) == set()
    assert github.labels(1430) == {LABEL}
    assert set(github.comments) == {1430}
    error = capsys.readouterr().err
    assert "issues/1357/comments" in error
    assert "HTTP 403" in error

    assert watch(github, sleep=no_sleep) == {1357: "flagged"}


def test_1281_a_new_conflict_after_a_fix_is_announced_again() -> None:
    github = FakeGitHub()
    github.add(1205, "CONFLICTING", "MERGEABLE", "CONFLICTING")
    # The first label fails, so no label removal marks the recovery.
    github.label_failures = 1

    assert watch(github, sleep=no_sleep) == {1205: "failed"}
    github.pulls[1205]["head"] = "1205b"  # The author merged main.
    assert watch(github, sleep=no_sleep) == {}
    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    assert len(github.comments[1205]) == 2
    assert "head=1205b" in github.comments[1205][1]


def test_1281_labels_clear_when_a_blocked_pr_merges_or_closes() -> None:
    github = FakeGitHub()
    github.add(1205, "MERGEABLE", labels=(LABEL,), state="MERGED")
    github.add(1206, "CONFLICTING", labels=(LABEL,), state="CLOSED")
    github.add(1207, "CONFLICTING", state="CLOSED")

    assert watch(github, sleep=no_sleep) == {1205: "cleared", 1206: "cleared"}
    assert github.labels(1205) == github.labels(1206) == set()


def test_1281_failed_label_creation_is_retried_for_the_next_pr() -> None:
    github = FakeGitHub()
    github.add(1205, "CONFLICTING")
    github.add(1206, "CONFLICTING")
    github.create_failures = 1

    assert watch(github, sleep=no_sleep) == {1205: "failed", 1206: "flagged"}
    assert github.labels(1206) == {LABEL}
    assert watch(github, sleep=no_sleep) == {1205: "flagged"}


def test_1281_a_pasted_marker_does_not_count_as_the_notification() -> None:
    github = FakeGitHub()
    github.add(1205, "CONFLICTING")
    github.timeline[1205] = [
        {
            "__typename": "IssueComment",
            "body": "Quoting: <!-- auto-merge-blocked head=1205a -->",
            "author": {"login": "someone"},
        }
    ]

    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    assert len(github.comments[1205]) == 1


def test_1281_a_labelled_pr_that_conflicts_again_on_a_new_head_is_told() -> None:
    github = FakeGitHub()
    github.add(1205, "CONFLICTING")

    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    # The author merged main, but main moved again before the next run.
    github.pulls[1205]["head"] = "1205b"
    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    assert watch(github, sleep=no_sleep) == {}
    assert len(github.comments[1205]) == 2
    assert github.labels(1205) == {LABEL}


def test_1281_a_conflict_recurring_on_the_same_head_is_told_again() -> None:
    # A main revert clears the conflict; a later main push brings it back.
    github = FakeGitHub()
    github.add(1205, "CONFLICTING", "MERGEABLE", "CONFLICTING")

    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    assert watch(github, sleep=no_sleep) == {1205: "cleared"}
    assert watch(github, sleep=no_sleep) == {1205: "flagged"}
    assert len(github.comments[1205]) == 2
