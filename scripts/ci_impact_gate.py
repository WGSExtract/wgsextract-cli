"""Plan only the authoritative regular Linux pytest selection."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ci_impact_diff import impact_diff
from scripts.ci_impact_shadow import compute, lane_tests, observation_map


def plan(
    root: Path,
    *,
    event: str,
    base: str,
    observations: Path,
    legacy: str,
    disabled: bool,
) -> dict[str, str | list[str]]:
    """The kill switch and evidence gaps use the untouched existing selection."""
    fallback = {"selection": json.loads(legacy) if legacy != "all" else "all"}
    if disabled or event not in {"pull_request", "push"}:
        return {**fallback, "mode": "legacy-disabled-or-outside-lane"}
    diff = impact_diff(root, base, "HEAD")
    if event == "push" and base != diff.base:
        return {**fallback, "mode": "legacy-non-linear-push"}
    evidence, gap = observation_map(root, observations, diff.base)
    if gap:
        return {**fallback, "mode": "legacy-observation-gap"}
    manifest = evidence["manifest"]
    recorded = manifest.get("recorded_at")
    if recorded is None:
        recorded = subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", manifest["source_sha"]],
            cwd=root,
            text=True,
        ).strip()
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(recorded)
    except (ValueError, TypeError):
        return {**fallback, "mode": "legacy-stale-map"}
    if not timedelta(minutes=-5) <= age <= timedelta(hours=48):
        return {**fallback, "mode": "legacy-stale-map"}
    tests = lane_tests(root, diff.head, skip_independent=event == "pull_request")
    candidates, reasons, unmatched = compute(root, diff, tests, evidence)
    if any(
        reason.startswith("universal-")
        for rules in reasons.values()
        for reason in rules
    ):
        return {"mode": "universal-full", "selection": "all"}
    if unmatched or not candidates["symbol"]:
        return {**fallback, "mode": "legacy-unmatched-or-empty"}
    return {"mode": "symbol", "selection": candidates["symbol"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--legacy", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--verify-wiring", action="store_true")
    args = parser.parse_args()
    decision = plan(
        Path.cwd().resolve(),
        event=args.event,
        base=args.base,
        observations=args.observations,
        legacy=args.legacy,
        disabled=args.disabled,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision) + "\n", encoding="utf-8")
    if args.verify_wiring:
        mode = decision["mode"]
        if mode in {"symbol", "universal-full"}:
            print(f"Verified fresh Linux selector map: {mode}.")
        else:
            print(
                f"::warning::Linux symbol gate did not activate: {mode}; using current selection."
            )
            raise SystemExit(3)


if __name__ == "__main__":
    main()
