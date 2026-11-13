"""Durable CI observations: canonical issues, audit comments and weekly rates."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.ci_github import TRUSTED, GitHub
from scripts.ci_policy import safe

LEDGER = "<!-- ci-flake-ledger:v1 -->"


class IssueReports:
    def __init__(self, github: GitHub) -> None:
        self.gh = github
        self._issues: list[dict[str, Any]] | None = None

    @property
    def issues(self) -> list[dict[str, Any]]:
        if self._issues is None:
            self._issues = [
                issue
                for issue in self.gh.pages("issues?state=all&labels=ci-automation")
                if issue["user"]["login"] in TRUSTED and "pull_request" not in issue
            ]
        return self._issues

    def find(self, marker: str) -> dict[str, Any] | None:
        return min(
            (issue for issue in self.issues if marker in (issue.get("body") or "")),
            key=lambda issue: int(issue["number"]),
            default=None,
        )

    def ensure_labels(self) -> None:
        labels = {item["name"] for item in self.gh.pages("labels")}
        for label, color in (("ci-automation", "ededed"), ("ci-red-main", "b60205")):
            if label not in labels:
                self.gh.api("POST", "labels", {"name": label, "color": color})

    def issue(
        self, title: str, marker: str, body: str, *, bug: bool = True
    ) -> dict[str, Any]:
        existing = self.find(marker)
        content = f"(AI): {marker}\n\n{body}"
        if existing:
            existing.update(
                self.gh.api(
                    "PATCH",
                    f"issues/{existing['number']}",
                    {"state": "open", "body": content},
                )
            )
            return existing
        self.ensure_labels()
        created: dict[str, Any] = self.gh.api(
            "POST",
            "issues",
            {
                "title": title[:240],
                "body": content,
                "labels": ["ci-automation", "bug"] if bug else ["ci-automation"],
            },
        )
        matches = [
            item
            for item in self.gh.pages("issues?state=all&labels=ci-automation")
            if item["user"]["login"] in TRUSTED and marker in (item.get("body") or "")
        ]
        canonical = min(matches or [created], key=lambda item: int(item["number"]))
        for duplicate in matches:
            if (
                duplicate["number"] != canonical["number"]
                and duplicate["state"] == "open"
            ):
                self.gh.api(
                    "PATCH", f"issues/{duplicate['number']}", {"state": "closed"}
                )
        self._issues = None
        return canonical

    def has_comment(self, issue: dict[str, Any], marker: str) -> bool:
        return any(
            marker in comment["body"] and comment["user"]["login"] in TRUSTED
            for comment in self.gh.pages(f"issues/{issue['number']}/comments")
        )

    def comment_once(self, issue: dict[str, Any], marker: str, body: str) -> bool:
        if self.has_comment(issue, marker):
            return False
        self.gh.api(
            "POST",
            f"issues/{issue['number']}/comments",
            {"body": f"(AI): {marker}\n\n{body}"},
        )
        return True

    def ledger(self) -> dict[str, Any]:
        issue = self.find(LEDGER)
        if issue is None:
            issue = self.issue(
                "CI flake ledger",
                LEDGER,
                "Automatic retry requests and outcomes. Records include workflow, job, "
                "step, first errors, classification, SHA and PR. "
                "Use the run links to compare attempts; never erase failed evidence.",
                bug=False,
            )
        pinned = self.gh.request(
            "POST",
            "graphql",
            {
                "query": "query($id:ID!){node(id:$id){... on Issue{isPinned}}}",
                "variables": {"id": issue["node_id"]},
            },
        )["data"]["node"]["isPinned"]
        if not pinned:
            self.gh.request(
                "POST",
                "graphql",
                {
                    "query": "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{id}}}",
                    "variables": {"id": issue["node_id"]},
                },
            )
        return issue

    def rollup(self, *, now: datetime | None = None) -> None:
        now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        since = now - timedelta(days=7)
        by_id: dict[int, dict[str, Any]] = {}
        for day in range(7):
            start = since + timedelta(days=day)
            for run in self.gh.runs_between(start, start + timedelta(days=1)):
                by_id[int(run["id"])] = run
        total = Counter(run["name"] for run in by_id.values())
        retried = Counter(
            run["name"] for run in by_id.values() if run["run_attempt"] > 1
        )
        self.comment_once(
            self.ledger(),
            f"<!-- ci-week:v2:{now.strftime('%G-%V')} -->",
            f"Seven days since {since:%Y-%m-%dT%H:%M:%SZ}. Run-level retry rate includes "
            "manual retries. Complete date-partitioned counts supersede any older "
            "single-search weekly calculation.\n\n"
            "| Workflow | Runs | Retried | Rate |\n|---|---:|---:|---:|\n"
            + "\n".join(
                f"| {safe(name).replace('|', '&#124;')} | {count} | {retried[name]} | "
                f"{retried[name] / count:.1%} |"
                for name, count in sorted(total.items())
                if name != "Rerun Lost Runners"
            ),
        )
        print(f"Counted {len(by_id)} distinct runs across {len(total)} workflows.")
