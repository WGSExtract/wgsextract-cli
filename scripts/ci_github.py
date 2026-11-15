"""GitHub API boundary for the CI failure observer (stdlib + installed gh only)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

TRUSTED = {"github-actions[bot]", "theontho"}


@dataclass(frozen=True)
class Evidence:
    failures: str = ""
    log: str = ""


class GitHub:
    def __init__(self, repository: str) -> None:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("Expected owner/repository")
        self.repository = repository
        self._evidence: dict[int, Evidence] = {}

    def request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> Any:
        command = ["gh", "api", "--method", method, path]
        if data is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command,
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def api(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        return self.request(method, f"repos/{self.repository}/{path}", data)

    def pages(
        self, path: str, key: str | None = None, *, max_pages: int = 100
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            separator = "&" if "?" in path else "?"
            response = self.api("GET", f"{path}{separator}per_page=100&page={page}")
            batch = response[key] if key else response
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise RuntimeError("Pagination limit exceeded; refusing an incomplete decision")

    def evidence(self, job: dict[str, Any]) -> Evidence:
        if job.get("conclusion") == "cancelled" and not job.get("steps"):
            return Evidence()
        job_id = int(job["id"])
        if job_id not in self._evidence:
            self._evidence[job_id] = self.fetch_evidence(job)
        return self._evidence[job_id]

    def fetch_evidence(self, job: dict[str, Any]) -> Evidence:
        """Only failed jobs need logs; gh handles signed redirects without token leaks."""
        check_url = job.get("check_run_url") or ""
        check_id = check_url.rsplit("/", 1)[-1]
        annotations = (
            self.pages(f"check-runs/{int(check_id)}/annotations")
            if check_id.isdecimal()
            else []
        )
        text = "\n".join(
            str(item.get("message", ""))
            for item in annotations
            if item.get("annotation_level") == "failure"
        )
        if re.search(
            r"lost communication|shutdown signal|Upstream package mirror unreachable",
            text,
            re.I,
        ) or not job.get("steps"):
            return Evidence(failures=text)
        return Evidence(failures=text, log=self.log(int(job["id"])))

    def log(self, job_id: int) -> str:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{self.repository}/actions/jobs/{job_id}/logs",
                "--allow-escape-sequences",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        # Expired/unavailable logs must never make an unknown failure retryable.
        return (
            re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
            if result.returncode == 0
            else ""
        )

    def runs_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Date-filtered run searches cap at 1,000; split before paginating."""
        interval = f"{start:%Y-%m-%dT%H:%M:%SZ}..{end:%Y-%m-%dT%H:%M:%SZ}"
        path = f"actions/runs?{urlencode({'created': interval})}&per_page=100"
        first = self.api("GET", path + "&page=1")
        if first["total_count"] >= 1000:
            if (end - start).total_seconds() <= 1:
                raise RuntimeError("Run search remains capped at one-second resolution")
            middle = start + (end - start) / 2
            return self.runs_between(start, middle) + self.runs_between(middle, end)
        runs: list[dict[str, Any]] = first["workflow_runs"]
        for page in range(2, (first["total_count"] + 99) // 100 + 1):
            runs.extend(self.api("GET", path + f"&page={page}")["workflow_runs"])
        if len(runs) != first["total_count"]:
            raise RuntimeError(
                "Run search changed during pagination; refusing incomplete rates"
            )
        return runs
