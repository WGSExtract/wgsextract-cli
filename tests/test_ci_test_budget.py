"""Ready-run cost projections use the same pytest XML as hosted shards."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_test_budget import make_baseline, read_junit, ready_summary

REGULAR = "tests/test_server_paths_schema.py"
DOCS = "tests/test_cli_help.py"
FILES = {REGULAR, DOCS}

# Pytest xunit2 records from Ubuntu nightly 45876381962, with durations
# adjusted in the policy scenarios so runner speed cannot affect the tests.
REGULAR_CASE = (
    '<testcase classname="tests.test_server_paths_schema" '
    'name="test_path_guard_covers_every_alignment_path[D:evil.fq-align-r1]" time="{}"/>'
)
DOCS_CASE = (
    '<testcase classname="tests.test_cli_help" '
    'name="test_1537_root_help_does_not_load_command_registration_modules" time="{}"/>'
)


def report(path: Path, *testcases: str) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest" tests="{len(testcases)}">'
        f"{''.join(testcases)}</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def test_1635_partial_shards_keep_unmeasured_cost_and_show_candidate_growth(
    tmp_path: Path,
) -> None:
    prior = report(
        tmp_path / "prior.xml", REGULAR_CASE.format(20), DOCS_CASE.format(60)
    )
    baseline = make_baseline(123, read_junit([prior], FILES))
    shard = report(
        tmp_path / "shard.xml",
        REGULAR_CASE.format(39),
        REGULAR_CASE.replace("-r1]", "-r2]").format(1),
    )
    cached_shard = report(tmp_path / "cached.xml")
    text = ready_summary(baseline, read_junit([shard, cached_shard], FILES), FILES)

    assert "carrying forward 1 unmeasured modules" in text
    assert "| CLI regular | 40.0 / 30 | +20.0 | 2 / 5 | candidate regression |" in text
    assert "| Full suite | 100.0 / 95 | +20.0 | 3 / 5 | over runtime budget |" in text


def test_1635_speedup_on_another_shard_offsets_area_regression(tmp_path: Path) -> None:
    prior = report(
        tmp_path / "prior.xml", REGULAR_CASE.format(20), DOCS_CASE.format(60)
    )
    baseline = make_baseline(123, read_junit([prior], FILES))
    slower = report(tmp_path / "slower.xml", REGULAR_CASE.format(40))
    faster = report(tmp_path / "faster.xml", DOCS_CASE.format(35))
    text = ready_summary(baseline, read_junit([slower, faster], FILES), FILES)

    assert "| CLI regular | 40.0 / 30 | +20.0 | 1 / 5 | suite offset |" in text
    assert "candidate regression" not in text


def test_1635_newly_skipped_tests_do_not_offset_growth(tmp_path: Path) -> None:
    prior = report(
        tmp_path / "prior.xml", REGULAR_CASE.format(20), DOCS_CASE.format(60)
    )
    baseline = make_baseline(123, read_junit([prior], FILES))
    ready = report(
        tmp_path / "ready.xml",
        REGULAR_CASE.format(40),
        DOCS_CASE.format(0).replace("/>", "><skipped/></testcase>"),
    )
    text = ready_summary(baseline, read_junit([ready], FILES), FILES)

    assert "Withheld speedup credit for 1" in text
    assert "| Full suite | 100.0 / 95 | +20.0 | 2 / 5 |" in text
    assert "candidate regression" in text
    assert "No regular pytest cases were measured" in ready_summary(baseline, {}, FILES)


@pytest.mark.parametrize("keep_skip_count", [False, True])
def test_1635_newly_running_cases_increase_cost_without_a_regression_signal(
    tmp_path: Path, keep_skip_count: bool
) -> None:
    other = REGULAR_CASE.replace("-r1]", "-r2]")
    prior = report(
        tmp_path / "prior.xml",
        REGULAR_CASE.format(0).replace("/>", "><skipped/></testcase>"),
        *([other.format(0)] if keep_skip_count else []),
    )
    baseline = make_baseline(123, read_junit([prior], FILES))
    ready = report(
        tmp_path / "ready.xml",
        REGULAR_CASE.format(20),
        *(
            [other.format(0).replace("/>", "><skipped/></testcase>")]
            if keep_skip_count
            else []
        ),
    )
    text = ready_summary(baseline, read_junit([ready], FILES), FILES)

    cases = 2 if keep_skip_count else 1
    assert (
        f"| CLI regular | 20.0 / 10 | +20.0 | {cases} / 5 | over runtime budget |"
        in text
    )
    assert "candidate regression" not in text


def test_1635_count_only_overage_is_advisory(tmp_path: Path) -> None:
    prior = report(tmp_path / "prior.xml", REGULAR_CASE.format(20))
    baseline = make_baseline(123, read_junit([prior], FILES))
    ready = report(
        tmp_path / "ready.xml",
        *(REGULAR_CASE.replace("-r1]", f"-r{i}]").format(1) for i in range(1, 7)),
    )
    text = ready_summary(baseline, read_junit([ready], FILES), FILES)

    assert "| CLI regular | 6.0 / 30 | -14.0 | 6 / 5 | over count target |" in text
    assert "candidate regression" not in text


def test_1635_calibration_refuses_a_lead_shard(tmp_path: Path) -> None:
    report(tmp_path / "pytest.xml", REGULAR_CASE.format(20))
    (tmp_path / "test-scope.json").write_text(
        f'{{"ran": ["{REGULAR}"], "skipped": {{}}}}', encoding="utf-8"
    )
    (tmp_path / "shard-count").write_text("2\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.ci_test_budget",
            "--calibrate-run",
            "123",
            "--nightly-artifact",
            str(tmp_path),
            "--baseline",
            str(baseline),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "unsharded nightly artifact" in result.stderr
    assert not baseline.exists()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (REGULAR_CASE.replace("/>", "><error/></testcase>"), "Unusable JUnit result"),
        (REGULAR_CASE.replace("tests.test_server", "tests.test_missing"), "unmapped"),
        (REGULAR_CASE.replace(' time="{}"', ""), "testcase time"),
        (REGULAR_CASE.format("NaN"), "testcase time"),
    ],
)
def test_1635_bad_receipts_do_not_produce_a_budget(
    tmp_path: Path, case: str, message: str
) -> None:
    junit = report(tmp_path / "pytest.xml", case.format(1))
    with pytest.raises(ValueError, match=message):
        read_junit([junit], FILES)
