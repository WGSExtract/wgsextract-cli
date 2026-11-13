# CI failure observation and retries

`Rerun Lost Runners` uses `python3 -m scripts.ci_failures` on the **default
branch**, never code or artifacts from the completed PR. It needs the runner's
installed Python and GitHub CLI, not Pixi or a project environment.

## Retry policy

Failures are classified from job conclusions, check annotations and failed-job
logs as **lost runner**, **mirror/network**, **test failure**, or **red main**.
Unknown errors stay test failures and are not automatically retried.

- A cancelled job with zero steps, or an explicit runner-loss annotation, gets
  **one** retry.
- Explicit package-mirror, DNS or connection failures get **two** retries.
  A named failing test is not reclassified as infrastructure merely because
  its traceback mentions a network error.
- The workflow never retries test failures. An operator/landing script can
  request up to **two** audited test retries with `--rerun-tests`.
- Deliberate cancellations after steps are left alone.
- Budgets count executions of each job across attempts. One job is retried per
  completion, leaving siblings alone; the next completion handles any remaining
  lost jobs. There is also a ten-attempt ceiling for automatic requests.
- Before retrying, the script verifies that this is the latest run and attempt
  for the workflow, event, branch **and head repository**. Fork branch names
  cannot accidentally supersede another repository's run.

Successful first-attempt PR runs are skipped by the workflow's job condition.
Supersession and zero-step cancellation cannot be determined from Actions'
job-condition context alone: they require a cheap run-list API lookup before
fetching jobs, logs or issues. There are no ten-minute sleeping runners.
Successful retries are observed to discover flakes; main successes close bugs.

## Pinned ledger and flaky tests

The script creates and pins **CI flake ledger**, labelled `ci-automation`, if
missing. A comment records every automatic retry request **before** dispatch,
including workflow, job, failing step, first five error lines, classification,
SHA, PR numbers, source attempt and a run link. A second comment confirms API
acceptance. API errors cannot produce an unaudited retry; a repeated observer
event cannot dispatch the same request twice.

This chooses immediate issue comments over summary-only batching: evidence is
searchable and durable even if a run is deleted, and agents can see a retry
without waiting a week. Monday's weekly comment adds the seven-day run-level
retry rate for each workflow (including manual retries, explicitly labelled).
Logs are quoted, HTML-escaped and mentions disabled before publication.

When a failed pytest node ID subsequently has a successful job **and the same
test step succeeds**, on the same immutable run SHA, the observer opens or
updates `Flaky: <test id>` with the `bug` label. A skipped step is not a pass.
Stable test-ID markers deduplicate across workflows and commits; concurrent
creation is reconciled to the oldest issue. Missing/expired logs do not invent
test IDs. The recorded fixtures under `tests/fixtures/ci_failures` exercise real
API responses, including the macOS GPU-resume flake.

## Red main

A latest failing push run on main opens or updates a workflow-specific
`main is red: <workflow>` bug with the failing pytest IDs and commit. Tests
already confirmed flaky by this observer are excluded. A later successful
main run closes that workflow's issue; an old/superseded success cannot close it.

Before any PR retry, the observer compares its failures with the latest completed
main push run of the same workflow. A queued fix is not evidence that main is
green. Any overlapping non-flaky test IDs quarantine
the PR with `ci-red-main` and prevent **all** retries of that run, including
landing-script test retries. Observations are tracked per workflow and SHA:
a successful observed retry clears only its own workflow's quarantine.
On a new PR head, a label-gated `pull_request_target` job invalidates the old
head's label; successful first-attempt PR completions still start no observer
runner. Closing a PR also clears the label. This trusted-base job only reads
issue comments and updates a label; it never runs the PR's code.
For the PR-only **PR area checks** router, job prefixes (`ubuntu /`, `macos /`,
`windows /`, `arm64 /`, `install /`) select the corresponding reusable workflow's
main push run, rather than looking for a nonexistent main run of the router.
The label and issue notify agents through the normal bug-first queue; no host
credentials or external notification service is needed.

Run an audited retry from the repository root:

```sh
python3 -m scripts.ci_failures --repository WGSExtract/wgsextract-cli \
  --run-id RUN_ID --rerun-tests
```

Exit status **2** means red main blocked the retry; **0** means processed
(possibly no retry needed); other failures must stop the landing loop. The
workflow treats quarantine as an observed condition, not another CI failure.
Use `--rollup` instead of `--run-id` for a weekly rate report.

The workflow has `actions: write` for retry requests, `checks: read` for
annotations, `issues: write` for bugs, labels and pinning, and `contents: read`
solely for the SHA-pinned checkout. No PR write token or additional secret is
needed.
