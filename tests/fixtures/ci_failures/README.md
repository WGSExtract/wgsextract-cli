Trimmed GitHub REST API responses recorded on 2026-11-13:

- `lost_runner.json`: job 131335012088, Install Lab on the help-budget PR;
  cancelled before its first step.
- `test_failure.json`: job 131653455642, CI macOS on main.
- `main_run.json`: run 43354532088 for that macOS job.
- `errors.json`: annotations and selected error lines from that job's log.
  Terminal colour escapes were removed; timestamps and test IDs are unchanged.

Tests change branch, attempt, conclusion and resource IDs to replay cancellation,
recovery and red-main scenarios without changing production GitHub resources.

The `lost_upstream_*` fixtures were recorded on 2026-11-14 from Install Lab run
43443418565 at commit `fca3bb08f9066b230aade01098c2f767dbabedae`, attempts 1 and 2.
Each jobs fixture retains all 12 job outcomes, runner identity and step
name/status/conclusion/number fields. Both attempts lost the macOS ARM64 Tauri
producer before its first step; the tree-app consumer then failed to download
`tauri-macos-arm64`. The annotation responses and selected error log lines are
recorded separately. Counterfactual regressions alter only the relevant job,
step or failure message.

`cancelled_draft_gate.json` records selected run/job fields from draft run
43579850651 on the worktree-cleanup PR, retrieved on 2026-11-14. Concurrency
cancelled its dependencies, but the `always()` summary reported failure.
Readiness tests replay that stale rollup alongside counterfactual replacements.
