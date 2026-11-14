# CI failure observation and retries

`Rerun Lost Runners` uses `python3 -m scripts.ci_failures` on the **default
branch**, never code or artifacts from the completed PR. It needs the runner's
installed Python and GitHub CLI, not Pixi or a project environment.

## Retry policy

Failures are classified as **lost runner**, **mirror/network**, **test failure**,
or **red main**. Only failure-level check annotations identify infrastructure
outages; warning annotations and test log quotations cannot trigger retries.
Failed-job logs supply test IDs and first error lines. Unknown errors stay test
failures and are not automatically retried.

- A cancelled job with zero steps, or an explicit runner-loss annotation, gets
  **one** retry.
- Explicit package-mirror, DNS or connection failures get **two** retries.
  A named failing test is not reclassified as infrastructure merely because
  its traceback mentions a network error.
- The workflow never retries test failures. An operator/landing script can
  request up to **two** audited test retries with `--rerun-tests`.
- Deliberate cancellations after steps are left alone.
- Budgets count executions of each job across attempts. A single current-attempt
  job is retried directly; multiple failed jobs use one failed-jobs request.
  Multiple cancelled jobs (or an older cancelled job left by a manual job-only
  retry) require a whole-run retry: GitHub rejects old job IDs and the
  failed-jobs endpoint does not guarantee retrying cancellations. This rare
  fallback can repeat successful siblings, and the ledger explicitly records
  its scope. Lost jobs still receive only one automatic retry.
- All outstanding failures must be eligible. A mixed test/infra failure needs a
  code fix, and a deliberately cancelled started job prevents restarting its
  queued zero-step siblings. No automatic request is made at attempt ten.
- Before retrying, the script verifies that this is the latest run and attempt
  for the workflow, event, branch **and head repository**. Fork branch names
  cannot accidentally supersede another repository's run.
  Same-head label-only router runs are separate validation scopes and do not
  supersede the full validation run.

Successful first-attempt PR runs are skipped by the workflow's job condition.
Supersession and zero-step cancellation cannot be determined from Actions'
job-condition context alone: they require a cheap run-list API lookup before
fetching jobs, logs or issues. There are no ten-minute sleeping runners.
Successful retries are observed to discover flakes; main successes close bugs.

## Pinned ledger and flaky tests

The script creates and pins **CI flake ledger — YYYY-Www** for the current ISO
week, labelled `ci-automation` and `ci-flake-ledger`. The period is included in
its marker. The weekly rollup pins the current issue and unpins/closes older
ledgers, preserving their comments as archives; the first retry of a new period
also advances an existing periodized ledger. The rollup migrates the original
unperiodized issue as well. Retry lookup reads only open ledger-labelled issues,
never the lifetime history of archived ledgers.

One comment records each run/attempt's retry request **before** dispatch,
including every failed job, failing step, first five error lines, classification,
SHA, PR numbers, source attempt and run link. The same comment is PATCHed with
API acceptance or an aborted-dispatch result. Comments are fetched just once
per audit, independent of the number of failed jobs, with a five-page
(500-comment) safety budget. A repeated observer event finds the same marker
without adding another record.

Auditing is best-effort: a missing/unavailable/oversized ledger or failed comment
write emits `ci-audit-skipped` in the workflow log and **does not block runner
recovery**. Confirmation-write failures likewise do not undo a successful retry.
Run/PR freshness checks, retry budgets and actual Actions API failures remain
mandatory; an audit outage does not bypass retry eligibility.

This chooses immediate issue comments over summary-only batching: evidence is
searchable and durable even if a run is deleted, and agents can see a retry
without waiting a week. Monday's weekly comment adds the seven-day run-level
retry rate for each workflow (including manual retries, explicitly labelled).
Date searches are partitioned by day and subdivided when GitHub's 1,000-result
cap is reached; boundary run IDs are deduplicated before computing rates.
Logs are quoted, HTML-escaped and mentions disabled before publication.

When a failed pytest node ID subsequently has a successful job **and the same
test step succeeds**, on the same immutable run SHA, the observer opens or
updates `Flaky: <test id>` with the `bug` label. A skipped step is not a pass.
Stable test-ID markers deduplicate across workflows and commits; concurrent
creation is reconciled to the oldest issue, including later lookups. Replaying
old recovery evidence never reopens a resolved bug. Only repository-owned heads
can create flaky-test exemptions: fork logs cannot change global retry policy.
Missing/expired logs do not invent
test IDs. The recorded fixtures under `tests/fixtures/ci_failures` exercise real
API responses, including the macOS GPU-resume flake.

## Red main

A latest failing push run on main opens or updates a workflow-specific
`main is red: <workflow>` bug with failing pytest IDs and commit. Non-pytest
failures name the job, step and first errors instead. Tests
already confirmed flaky by this observer are excluded. A later successful
main run closes that workflow's issue; an old/superseded success cannot close it.

Before any PR retry, the observer compares its failures with the latest conclusive
(success or failure) main push run of the same workflow. Neither a queued fix
nor a cancelled run is evidence that main is green. Overlapping non-flaky IDs,
or matching job/step/error signatures when neither side has IDs, quarantine
the PR with `ci-red-main` and prevent **all** retries of that run, including
landing-script test retries. Observations are tracked per workflow and SHA:
a successful observed retry clears only its own workflow's quarantine.
On a new PR head, a label-gated `pull_request_target` job invalidates the old
head's label; successful first-attempt PR completions still start no observer
runner. Closing a PR also clears the label. This trusted-base job only reads
issue comments and updates a label; it never runs the PR's code.
Every reconciliation checks the PR's live state and head. A late observer cannot
restore an old-head or closed-PR label. Workflow executions serialize issue and
label writes with `concurrency.queue: max`, rather than dropping pending
observers, and label reconciliation re-reads committed state to handle local
landing callers as well.
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
annotations, `issues: write` for bugs, labels and pinning, `pull-requests: read`
to verify the live head/state, and `contents: read` solely for the SHA-pinned
checkout. No PR write token or additional secret is needed.

GitHub supports [`queue: max`](https://docs.github.com/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/control-the-concurrency-of-workflows-and-jobs),
but current actionlint 1.7.12 does not recognize that schema key. The path-scoped
actionlint configuration exempts only that exact unknown-key diagnostic;
`validate_github_actions.py` validates the value and disallows conflicting
`cancel-in-progress` settings. All other actionlint checks remain enabled.
