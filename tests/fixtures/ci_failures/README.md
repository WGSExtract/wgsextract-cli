Trimmed GitHub REST API responses recorded on 2026-11-13:

- `lost_runner.json`: job 131335012088, Install Lab on the help-budget PR;
  cancelled before its first step.
- `test_failure.json`: job 131653455642, CI macOS on main.
- `main_run.json`: run 43354532088 for that macOS job.
- `errors.json`: annotations and selected error lines from that job's log.
  Terminal colour escapes were removed; timestamps and test IDs are unchanged.

Tests change branch, attempt, conclusion and resource IDs to replay cancellation,
recovery and red-main scenarios without changing production GitHub resources.
