"""Flag pull requests whose auto-merge is stuck on a conflict with their base (#1281).

GitHub never adds a conflicting pull request to the merge queue, even with
auto-merge enabled, and says nothing about it. This labels each such pull
request, asks its author once per blockage to merge the base branch, and
clears the label when the conflict is gone or the PR closes. It only calls the GitHub
API (stdlib + gh).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from scripts.ci_github import GitHub

LABEL = "auto-merge-blocked"
# One comment per head per blockage: a new head or a cleared label re-arms it.
MARKER = "<!-- auto-merge-blocked head={head} -->"
# Only the workflow's own comments count; anyone can paste the marker.
WATCHER = {"github-actions", "github-actions[bot]"}
COMMENT = (
    MARKER + "\nAuto-merge is enabled, but this pull request conflicts with "
    "`{base}`, so GitHub won't add it to the merge queue. Merge `{base}` into "
    "this branch (or rebase onto it) and resolve the conflicts. The "
    f"`{LABEL}` label is removed automatically once the conflict is gone."
)
QUERY = """
query($owner: String!, $name: String!, $label: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    label(name: $label) { name }
    closed: pullRequests(states: [CLOSED, MERGED], labels: [$label], first: 100) {
      nodes { number baseRefName }
    }
    pullRequests(states: OPEN, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        mergeable
        baseRefName
        headRefOid
        autoMergeRequest { enabledAt }
        labels(first: 100) { nodes { name } }
      }
    }
  }
}
"""
# Comments and label removals, newest last: was this blockage announced yet?
TIMELINE = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      timelineItems(last: 100, itemTypes: [ISSUE_COMMENT, UNLABELED_EVENT]) {
        nodes {
          __typename
          ... on IssueComment { body author { login } }
          ... on UnlabeledEvent { label { name } }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Pull:
    number: int
    mergeable: str
    base: str
    auto_merge: bool
    labelled: bool
    head: str = ""

    @property
    def blocked(self) -> bool:
        return self.auto_merge and self.mergeable == "CONFLICTING"

    @property
    def pending(self) -> bool:
        """Only auto-merging PRs need a mergeability verdict."""
        return self.auto_merge and self.mergeable == "UNKNOWN"


def graphql(github: GitHub, query: str, **variables: Any) -> Any:
    owner, name = github.repository.split("/")
    response = github.request(
        "POST",
        "graphql",
        {"query": query, "variables": {"owner": owner, "name": name, **variables}},
    )
    if response.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {response['errors']}")
    return response["data"]["repository"]


def open_pulls(github: GitHub) -> tuple[list[Pull], bool]:
    """Open PRs and labelled closed ones, and whether the label exists yet."""
    pulls: list[Pull] = []
    cursor = None
    while True:
        repository = graphql(github, QUERY, label=LABEL, cursor=cursor)
        if cursor is None:
            pulls += [
                Pull(int(node["number"]), "CLOSED", node["baseRefName"], False, True)
                for node in repository["closed"]["nodes"]
            ]
        page = repository["pullRequests"]
        pulls += [pull(node) for node in page["nodes"]]
        if not page["pageInfo"]["hasNextPage"]:
            return pulls, repository["label"] is not None
        cursor = page["pageInfo"]["endCursor"]


def pull(node: dict[str, Any]) -> Pull:
    return Pull(
        number=int(node["number"]),
        mergeable=str(node["mergeable"]),
        base=str(node["baseRefName"]),
        auto_merge=node["autoMergeRequest"] is not None,
        labelled=any(label["name"] == LABEL for label in node["labels"]["nodes"]),
        head=str(node["headRefOid"]),
    )


def announced(github: GitHub, pr: Pull) -> bool:
    """The watcher told the author about this head since the label last cleared."""
    marker = MARKER.format(head=pr.head)
    items = graphql(github, TIMELINE, number=pr.number)["pullRequest"]
    for item in reversed(items["timelineItems"]["nodes"]):
        if item["__typename"] == "UnlabeledEvent" and item["label"]["name"] == LABEL:
            return False
        if (
            item["__typename"] == "IssueComment"
            and (item["author"] or {}).get("login") in WATCHER
            and marker in item["body"]
        ):
            return True
    return False


def watch(
    github: GitHub,
    *,
    attempts: int = 6,
    delay: float = 15,
    sleep: Callable[[float], None] = time.sleep,
    dry_run: bool = False,
) -> dict[int, str]:
    """Label and comment on newly blocked PRs; unlabel unblocked ones."""
    for attempt in range(attempts):
        pulls, label_exists = open_pulls(github)
        # GitHub computes mergeability lazily after the base moves.
        if attempt + 1 == attempts or not any(p.pending for p in pulls):
            break
        sleep(delay)
    actions: dict[int, str] = {}
    for pr in pulls:
        if pr.pending:
            actions[pr.number] = "unknown"
            continue
        if not pr.blocked and not pr.labelled:
            continue
        if dry_run:
            if pr.blocked != pr.labelled:
                actions[pr.number] = "flagged" if pr.blocked else "cleared"
            continue
        # One PR's API failure must not stop the others; main reports it.
        try:
            if not pr.blocked:
                github.api("DELETE", f"issues/{pr.number}/labels/{LABEL}")
                actions[pr.number] = "cleared"
                continue
            if not pr.labelled and not label_exists:
                github.api(
                    "POST",
                    "labels",
                    {
                        "name": LABEL,
                        "color": "d93f0b",
                        "description": "Auto-merge is enabled but the PR "
                        "conflicts with its base; merge the base branch",
                    },
                )
                label_exists = True
            if flag(github, pr):
                actions[pr.number] = "flagged"
        except subprocess.CalledProcessError as error:
            # gh passes the body over stdin; its argv identifies the endpoint.
            detail = (error.stderr or "").strip() or str(error)
            print(f"#{pr.number}: {error.cmd}: {detail}", file=sys.stderr)
            actions[pr.number] = "failed"
        except RuntimeError as error:
            print(f"#{pr.number}: {error}", file=sys.stderr)
            actions[pr.number] = "failed"
    return actions


def flag(github: GitHub, pr: Pull) -> bool:
    """Tell the author about this head, then label; whether anything changed."""
    # Comment before labelling so a failed run is retried; the marker stops a
    # retry after a failed label from repeating the comment. A labelled PR's
    # new head (the author merged main, but it conflicts again) is announced.
    changed = False
    if not announced(github, pr):
        github.api(
            "POST",
            f"issues/{pr.number}/comments",
            {"body": COMMENT.format(base=pr.base, head=pr.head)},
        )
        changed = True
    if not pr.labelled:
        github.api("POST", f"issues/{pr.number}/labels", {"labels": [LABEL]})
        changed = True
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="OWNER/REPO")
    parser.add_argument(
        "--settle",
        type=float,
        default=0,
        help="Seconds to wait first, so GitHub sees a just-pushed base.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report, but change nothing."
    )
    args = parser.parse_args(argv)
    time.sleep(args.settle)
    actions = watch(GitHub(args.repository), dry_run=args.dry_run)
    for number, action in sorted(actions.items()):
        print(f"#{number}: {action}")
    print(f"{sum(a == 'flagged' for a in actions.values())} newly blocked PR(s).")
    return 1 if "failed" in actions.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
