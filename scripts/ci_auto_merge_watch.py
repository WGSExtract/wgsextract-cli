"""Flag pull requests whose auto-merge is stuck on a conflict with their base (#1281).

GitHub never adds a conflicting pull request to the merge queue, even with
auto-merge enabled, and says nothing about it. This labels each such pull
request, asks its author once to merge the base branch, and clears the label
when the conflict is gone. It only calls the GitHub API (stdlib + gh).
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from scripts.ci_github import GitHub

LABEL = "auto-merge-blocked"
MARKER = "<!-- auto-merge-blocked -->"
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
    pullRequests(states: OPEN, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        mergeable
        baseRefName
        autoMergeRequest { enabledAt }
        labels(first: 100) { nodes { name } }
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

    @property
    def blocked(self) -> bool:
        return self.auto_merge and self.mergeable == "CONFLICTING"

    @property
    def watched(self) -> bool:
        return self.auto_merge or self.labelled


def open_pulls(github: GitHub) -> tuple[list[Pull], bool]:
    """Every open pull request, and whether the label exists yet."""
    owner, name = github.repository.split("/")
    pulls: list[Pull] = []
    cursor = None
    while True:
        response = github.request(
            "POST",
            "graphql",
            {
                "query": QUERY,
                "variables": {
                    "owner": owner,
                    "name": name,
                    "label": LABEL,
                    "cursor": cursor,
                },
            },
        )
        if response.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {response['errors']}")
        repository = response["data"]["repository"]
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
    )


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
        if attempt + 1 == attempts or not any(
            p.watched and p.mergeable == "UNKNOWN" for p in pulls
        ):
            break
        sleep(delay)
    actions: dict[int, str] = {}
    for pr in pulls:
        if pr.mergeable == "UNKNOWN":
            if pr.watched:
                actions[pr.number] = "unknown"
            continue
        if pr.blocked and not pr.labelled:
            actions[pr.number] = "flagged"
            if dry_run:
                continue
            if not label_exists:
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
            # Label first: a failed comment must not be repeated on every run.
            github.api("POST", f"issues/{pr.number}/labels", {"labels": [LABEL]})
            github.api(
                "POST",
                f"issues/{pr.number}/comments",
                {"body": COMMENT.format(base=pr.base)},
            )
        elif pr.labelled and not pr.blocked:
            actions[pr.number] = "cleared"
            if not dry_run:
                github.api("DELETE", f"issues/{pr.number}/labels/{LABEL}")
    return actions


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
