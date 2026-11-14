"""Durable CI observations: canonical issues, audit comments and weekly rates."""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.ci_github import TRUSTED, GitHub
from scripts.ci_policy import safe

LEDGER = "<!-- ci-flake-ledger:"


@dataclass(frozen=True)
class AuditReceipt:
    duplicate: bool = False
    comment_id: int | None = None


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
        for label, color in (
            ("ci-automation", "ededed"),
            ("ci-red-main", "b60205"),
            ("ci-flake-ledger", "5319e7"),
        ):
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

    def set_pinned(self, issue: dict[str, Any], desired: bool) -> None:
        pinned = self.gh.request(
            "POST",
            "graphql",
            {
                "query": "query($id:ID!){node(id:$id){... on Issue{isPinned}}}",
                "variables": {"id": issue["node_id"]},
            },
        )["data"]["node"]["isPinned"]
        if pinned != desired:
            mutation = "pinIssue" if desired else "unpinIssue"
            self.gh.request(
                "POST",
                "graphql",
                {
                    "query": f"mutation($id:ID!){{{mutation}(input:{{issueId:$id}}){{issue{{id}}}}}}",
                    "variables": {"id": issue["node_id"]},
                },
            )

    def ledger(
        self, *, now: datetime | None = None, rotate_legacy: bool = False
    ) -> dict[str, Any]:
        year, week, _ = (now or datetime.now(timezone.utc)).isocalendar()
        period = f"{year}-W{week:02d}"
        marker = f"{LEDGER}v2:{period} -->"
        ledgers = self.gh.pages("issues?state=open&labels=ci-flake-ledger")
        if rotate_legacy:
            ledgers += self.gh.pages("issues?state=open&labels=ci-automation")
        ledgers = [
            item
            for item in {int(item["number"]): item for item in ledgers}.values()
            if item["user"]["login"] in TRUSTED
            and LEDGER in (item.get("body") or "")
            and "pull_request" not in item
        ]
        issue = min(
            (item for item in ledgers if marker in item["body"]),
            key=lambda item: int(item["number"]),
            default=None,
        )
        if issue is None:
            self.ensure_labels()
            issue = self.gh.api(
                "POST",
                "issues",
                {
                    "title": f"CI flake ledger — {period}",
                    "body": f"(AI): {marker}\n\nAutomatic retry requests and outcomes for "
                    f"{period}. Records include workflow, job, step, first errors, "
                    "classification, SHA and PR. Earlier periods remain archived.",
                    "labels": ["ci-automation", "ci-flake-ledger"],
                },
            )
            ledgers += self.gh.pages("issues?state=open&labels=ci-flake-ledger")
            issue = min(
                (
                    item
                    for item in ledgers
                    if item["user"]["login"] in TRUSTED
                    and marker in (item.get("body") or "")
                    and "pull_request" not in item
                ),
                key=lambda item: int(item["number"]),
                default=issue,
            )
        for old in {int(item["number"]): item for item in ledgers}.values():
            if (
                old["number"] != issue["number"]
                and old["user"]["login"] in TRUSTED
                and LEDGER in (old.get("body") or "")
                and "pull_request" not in old
            ):
                self.set_pinned(old, False)
                self.gh.api("PATCH", f"issues/{old['number']}", {"state": "closed"})
        self.set_pinned(issue, True)
        self._issues = None
        return issue

    def audit_warning(self, stage: str, error: Exception) -> None:
        detail = str(error).replace("\n", " ")[:200]
        print(
            f"::warning::ci-audit-skipped ({stage}): {type(error).__name__}: {detail}",
            file=sys.stderr,
        )

    def begin_retry_audit(self, marker: str, body: str) -> AuditReceipt:
        try:
            issue = self.ledger()
            comments = self.gh.pages(f"issues/{issue['number']}/comments", max_pages=5)
            for comment in comments:
                if marker in comment["body"] and comment["user"]["login"] in TRUSTED:
                    accepted = comment["body"].startswith(
                        f"(AI): {marker}\n\nRetry accepted:"
                    )
                    return AuditReceipt(
                        duplicate=accepted, comment_id=int(comment["id"])
                    )
            comment = self.gh.api(
                "POST", f"issues/{issue['number']}/comments", {"body": body}
            )
            return AuditReceipt(comment_id=int(comment["id"]))
        except Exception as error:
            # Audit availability must never gate recovery of a lost runner.
            self.audit_warning("create", error)
            return AuditReceipt()

    def finish_retry_audit(self, receipt: AuditReceipt, body: str) -> None:
        if receipt.comment_id is not None:
            try:
                self.gh.api(
                    "PATCH", f"issues/comments/{receipt.comment_id}", {"body": body}
                )
            except Exception as error:
                self.audit_warning("update", error)

    def rollup(self, *, now: datetime | None = None) -> None:
        now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        ledger = self.ledger(now=now, rotate_legacy=True)
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
            ledger,
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
