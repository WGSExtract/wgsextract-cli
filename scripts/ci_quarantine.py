"""PR-wide red-main labels backed by per-workflow, per-head observations."""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.ci_github import TRUSTED, GitHub

STATE = re.compile(r"<!-- ci-quarantine (\{.*\}) -->")


class Quarantine:
    def __init__(self, github: GitHub) -> None:
        self.gh = github

    def states(self, pr: int) -> dict[int, dict[str, Any]]:
        states: dict[int, dict[str, Any]] = {}
        for comment in self.gh.pages(f"issues/{pr}/comments"):
            if comment["user"]["login"] not in TRUSTED:
                continue
            match = STATE.search(comment["body"])
            if match:
                state = json.loads(match[1])
                states[int(state["workflow"])] = state
        return states

    def label(self, pr: int, sha: str, states: dict[int, dict[str, Any]]) -> None:
        active = any(
            state["sha"] == sha and state["active"] for state in states.values()
        )
        labels = self.gh.pages(f"issues/{pr}/labels")
        labelled = any(label["name"] == "ci-red-main" for label in labels)
        if active and not labelled:
            self.gh.api("POST", f"issues/{pr}/labels", {"labels": ["ci-red-main"]})
        elif not active and labelled:
            self.gh.api("DELETE", f"issues/{pr}/labels/ci-red-main")

    def update(self, run: dict[str, Any], *, active: bool) -> None:
        for pr in run.get("pull_requests", []):
            number = int(pr["number"])
            states = self.states(number)
            workflow = int(run["workflow_id"])
            state = {"workflow": workflow, "sha": run["head_sha"], "active": active}
            if states.get(workflow) != state:
                # Successful unrelated workflows cannot clear another workflow's
                # quarantine; state is scoped to the immutable source head.
                self.gh.api(
                    "POST",
                    f"issues/{number}/comments",
                    {
                        "body": f"(AI): <!-- ci-quarantine {json.dumps(state)} -->\n\n"
                        + (
                            "Red main matches this workflow; retries blocked."
                            if active
                            else "This workflow no longer matches red main."
                        )
                        + f"\nhttps://github.com/{self.gh.repository}/actions/runs/{run['id']}"
                    },
                )
                states[workflow] = state
            self.label(number, run["head_sha"], states)

    def reset(self, pr: int, head_sha: str) -> None:
        """A new head invalidates old observations without starting a success job."""
        self.label(pr, head_sha, self.states(pr))
