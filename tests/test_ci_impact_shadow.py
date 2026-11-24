"""Regressions for the #1339 advisory Linux report."""

from __future__ import annotations

import gzip
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_impact_observations import SCHEMA
from scripts.ci_impact_results import file_results

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.parametrize(
    "gate_receipt",
    [None, '{"mode": "symbol",', '{"mode": "symbol", "selection": {"bad": true}}'],
    ids=["absent", "truncated", "invalid-selection"],
)
def test_shadow_reports_source_scans_and_base_only_failures(
    tmp_path: Path, gate_receipt: str | None
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "config", "core.autocrlf", "false")
    cmd_path = "src/wgsextract_cli/commands/writer.py"
    fixture_files = {
        cmd_path: "def write(path):\n    path.write_text('ok\\n')\n",
        "tests/test_text_newlines.py": "def test_newline():\n    assert False\n",
        "tests/test_command_needs.py": "def test_needs():\n    pass\n",
        "tests/test_writer.py": "def test_writer():\n    pass\n",
        "tests/test_unrun.py": "def test_unrun():\n    pass\n",
        "tests/test_qualify_30x.py": "def test_qualify():\n    pass\n",
    }
    for file, contents in fixture_files.items():
        target = tmp_path / file
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(contents)
    # #1515: the observation hash describes the Git blob, not host text newlines.
    (tmp_path / "pixi.lock").write_bytes(b"lock\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Common base")
    _git(tmp_path, "switch", "-c", "pr")
    (tmp_path / cmd_path).write_text("def write(path):\n    path.write_text('bad')\n")
    _git(tmp_path, "commit", "-am", "Edit package writer")
    pr_head = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "switch", "main")
    (tmp_path / "tests/test_qualify_30x.py").write_text(
        "def test_qualify():\n    assert False\n"
    )
    _git(tmp_path, "commit", "-am", "Introduce base-only failure")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "merge", "--no-ff", "-m", "Synthetic PR test tree", "pr")

    output = tmp_path / "out"
    output.mkdir()
    xml = output / "pytest.xml"
    executed = [
        "tests/test_text_newlines.py",
        "tests/test_qualify_30x.py",
        "tests/test_writer.py",
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "addopts=",
            "--basetemp=out/pytest",
            f"--junitxml={xml}",
            *executed,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    evidence = output / "map.json.gz"
    with gzip.open(evidence, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "manifest": {
                    "source_sha": base,
                    "schema": SCHEMA,
                    "platform": "Linux",
                    "machine": "x86_64",
                    "python": platform.python_version(),
                    "run_id": 1,
                    "attempt": 1,
                    "profile": "ubuntu-x64/all/skip-independent=false",
                    "lock_sha256": hashlib.sha256(b"lock\n").hexdigest(),
                },
                "observed_tests": ["tests/test_writer.py"],
                "modules": {cmd_path: ["tests/test_writer.py"]},
                "functions": {cmd_path: {"write": ["tests/test_writer.py"]}},
                "symbol_reads": {},
                "seconds": {"tests/test_writer.py": 6.0},
            },
            stream,
        )
    gate = output / "gate.json"
    if gate_receipt is not None:
        gate.write_text(gate_receipt)
    cli = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "from scripts.ci_impact_shadow import main; main()",
            str(ROOT),
            "--selection",
            "all",
            "--outcome",
            "failure",
            "--observations",
            str(evidence),
            "--junit",
            str(xml),
            "--summary",
            str(output / "summary.md"),
            "--output",
            str(output / "report.json"),
            "--run-id",
            "1",
            "--attempt",
            "1",
            "--gate-decision",
            str(gate),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["tested_base"] == base
    assert report["pr_head"] == pr_head
    assert report["observation_gap"] is None, report["observation_gap"]
    if gate_receipt is None:
        assert report["comparable"]
    else:
        assert not report["comparable"]
        assert report["gate_mode"] == "unknown-invalid-decision"
    assert "tests/test_unrun.py" not in report["actual_results"]
    line = report["variants"].pop("line")
    assert line["status"] == "no-paired-map"
    assert line["selected"] == report["effective_selected"]
    assert line["nightly_recall"] is None
    for variant in report["variants"].values():
        assert {"tests/test_text_newlines.py", "tests/test_command_needs.py"} <= set(
            variant["selected"]
        )
        assert variant["observed_omitted_failures"] == ["tests/test_qualify_30x.py"]
        assert variant["estimated_test_minutes"] == 0.1
    assert "not proof of PR origin" in (output / "summary.md").read_text()


@pytest.mark.skipif(
    platform.system() != "Linux" or platform.machine() != "x86_64",
    reason="trusted Linux x64 coverage profile",
)
@pytest.mark.parametrize(
    "change", ["replacement", "fixture-drift", "insertion", "corrupt-cache"]
)
def test_1452_line_shadow_uses_prior_base_contexts_without_deduplicating_tests(
    tmp_path: Path,
    change: str,
) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    source = "src/wgsextract_cli/branchy.py"
    test_a, test_b, test_c, contract = [
        f"tests/test_{name}.py" for name in ("left", "also_left", "right", "branchy")
    ]
    original = "def branchy(left):\n    if left:\n        return 1\n    return 2\n"
    files = {
        source: original,
        "pytest.ini": "[pytest]\npythonpath = src/wgsextract_cli\n",
        "pixi.lock": "lock\n",
        "test-data/frozen.txt": "fixture",
        "tests/test_text_newlines.py": "def test_newlines(): pass\n",
        contract: "def test_contract(): pass\n",
        **{
            test: "from branchy import branchy\n\ndef test_branch():\n"
            f"    assert branchy({arg}) == {expected}\n"
            for test, arg, expected in (
                (test_a, True, 1),
                (test_b, True, 1),
                (test_c, False, 2),
            )
        },
    }
    for path, contents in files.items():
        target = tmp_path / path
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(contents)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Nightly tested base")

    def run(*args: str) -> str:
        result = subprocess.run(
            [sys.executable, *args],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def script(name: str, *args: str) -> str:
        return run(
            "-c",
            "import importlib, sys; sys.path.insert(0, sys.argv.pop(1)); "
            "importlib.import_module(sys.argv.pop(1)).main()",
            str(ROOT),
            f"scripts.ci_impact_{name}",
            *args,
        )

    (tmp_path / "out").mkdir()
    # The lean merge-queue policy lane installs coverage, not pytest-cov.
    run(
        "-c",
        """
import sys
import pytest
from coverage import Coverage

cov = Coverage(config_file=False, data_file="out/.coverage",
               include=["*/src/wgsextract_cli/*"])
cov.set_option("run:core", "ctrace")

class Contexts:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        cov.switch_context(item.nodeid + "|run")
        try:
            yield
        finally:
            cov.switch_context("")

with cov.collect():
    status = pytest.main(sys.argv[1:], plugins=[Contexts()])
cov.save()
sys.exit(status)
""",
        "-q",
        "--rootdir=.",
        "-p",
        "no:pytest_cov",
        "-o",
        "addopts=",
        "-c",
        "pytest.ini",
        "--basetemp=out/pytest",
        "--junitxml=out/nightly.xml",
    )
    metadata = ("--coverage-file", "out/.coverage", "--run-id", "1", "--attempt", "1")
    script(
        "observations",
        *metadata,
        "--junit",
        "out/nightly.xml",
        "--output",
        "out/map.gz",
    )
    assert "bytes" in script("lines", *metadata, "--output", "out/lines.gz")
    if change == "corrupt-cache":
        # A gzip header followed by the reserved DEFLATE block type.
        (tmp_path / "out/lines.gz").write_bytes(b"\x1f\x8b\x08\x00\0\0\0\0\0\xff\x07")
    if change == "fixture-drift":
        (tmp_path / "test-data/frozen.txt").write_text("different fixture")
        _git(tmp_path, "commit", "-am", "Change an input outside tests")
    _git(tmp_path, "switch", "-c", "pr")
    changed = (
        original.replace("    return 2", "    left = False\n    return 2")
        if change == "insertion"
        else original.replace("return 1", "return 10")
    )
    (tmp_path / source).write_text(changed)
    _git(tmp_path, "commit", "-am", "Edit one body")
    _git(tmp_path, "switch", "main")
    _git(tmp_path, "merge", "--no-ff", "-m", "Synthetic PR tree", "pr")
    script(
        "gate",
        "--event",
        "pull_request",
        "--base",
        "HEAD^1",
        "--legacy",
        "all",
        "--observations",
        "out/map.gz",
        "--output",
        "out/gate.json",
    )
    script(
        "shadow",
        "--observations",
        "out/map.gz",
        "--line-map",
        "out/lines.gz",
        "--junit",
        "out/not-run.xml",
        "--summary",
        "out/summary.md",
        "--output",
        "out/report.json",
        "--selection",
        "all",
        "--outcome",
        "skipped",
        "--run-id",
        "2",
        "--attempt",
        "1",
        "--gate-decision",
        "out/gate.json",
    )
    report = json.loads((tmp_path / "out/report.json").read_text())
    line = report["variants"]["line"]
    if change == "replacement":
        assert report["gate_mode"] == "symbol"
        assert {test_a, test_b, test_c, contract} <= set(report["effective_selected"])
        assert line["status"] == "eligible"
        assert set(line["selected"]) == set(report["effective_selected"]) - {test_c}
        assert line["estimated_test_minutes"] is not None
    else:
        assert (
            line["status"]
            == {
                "fixture-drift": "input-drift",
                "insertion": "unanchored-edit",
                "corrupt-cache": "invalid-or-unavailable-map",
            }[change]
        )
        assert line["selected"] == report["effective_selected"]


def test_foreign_observation_map_is_not_comparable(tmp_path: Path) -> None:
    from scripts.ci_impact_shadow import observation_map

    _git(tmp_path, "init")
    evidence = tmp_path / "stale.json.gz"
    with gzip.open(evidence, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "manifest": {
                    "source_sha": "a" * 40,
                    "schema": SCHEMA,
                    "platform": "Linux",
                    "machine": "x86_64",
                    "python": platform.python_version(),
                    "run_id": 1,
                    "attempt": 1,
                    "profile": "ubuntu-x64/all/skip-independent=false",
                },
                "observed_tests": ["tests/test_legacy.py"],
            },
            stream,
        )
    observations, gap = observation_map(tmp_path, evidence, "b" * 40)
    assert not observations
    assert gap and "outside" in gap


@pytest.mark.parametrize(
    ("before", "after", "test_import", "broad"),
    [
        (
            "import sys\n\ndef report():\n    return sys.platform\n",
            "import os as sys\n\ndef report():\n    return sys.platform\n",
            "",
            False,
        ),
        (
            "import sys\nLABEL = sys.platform\n",
            "import os as sys\nLABEL = sys.platform\n",
            "",
            True,
        ),
        (
            "from sys import platform as LABEL\n",
            "from os import name as LABEL\n",
            "from wgsextract_cli.info import LABEL\n",
            False,
        ),
        ("BASE = 1\nDERIVED = BASE * 2\n", "BASE = 2\nDERIVED = BASE * 2\n", "", True),
        (
            "BASE = 1\ndef report(value=BASE):\n    return value\n",
            "BASE = 2\ndef report(value=BASE):\n    return value\n",
            "",
            True,
        ),
    ],
)
def test_symbol_keeps_import_and_initialization_consumers(
    tmp_path: Path,
    before: str,
    after: str,
    test_import: str,
    broad: bool,
) -> None:
    from scripts.ci_impact_diff import impact_diff
    from scripts.ci_impact_shadow import compute

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    src = "src/wgsextract_cli/info.py"
    target = tmp_path / src
    target.parent.mkdir(parents=True)
    target.write_text(before)
    tested = {"tests/test_report.py", "tests/test_other_api.py"}
    for test in tested:
        path = tmp_path / test
        path.parent.mkdir(exist_ok=True)
        import_line = test_import if test == "tests/test_report.py" else ""
        if import_line:
            for file, source in (
                ("facade.py", "from .info import LABEL\n"),
                ("outer.py", "from .facade import LABEL\n"),
                ("entry.py", "from .outer import LABEL\n"),
            ):
                (target.parent / file).write_text(source)
            import_line = import_line.replace(".info import", ".entry import")
        path.write_text(import_line + "def test_contract():\n    pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Existing observed report")
    target.write_text(after)
    _git(tmp_path, "commit", "-am", "Change reader input")

    candidates, _, _ = compute(
        tmp_path,
        impact_diff(tmp_path, "HEAD^1", "HEAD"),
        tested,
        {
            "functions": {src: {"report": ["tests/test_report.py"]}},
            "modules": {src: sorted(tested)},
        },
    )
    assert set(candidates["decisive"]) == tested
    expected = tested if broad else {"tests/test_report.py"}
    assert set(candidates["symbol"]) == expected
    assert set(candidates["widely_called"]) == expected


@pytest.mark.parametrize("edit", ["deleted function", "changed external default"])
def test_selection_keeps_observed_interface_users(tmp_path: Path, edit: str) -> None:
    from scripts.ci_impact_diff import impact_diff
    from scripts.ci_impact_shadow import compute

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    src = "src/wgsextract_cli/info.py"
    target = tmp_path / src
    target.parent.mkdir(parents=True)
    caller = "src/wgsextract_cli/commands/lookup.py"
    (tmp_path / caller).parent.mkdir()
    default = edit == "changed external default"
    target.write_text("VALUE = 1\n" if default else "def lookup():\n    return 1\n")
    (tmp_path / caller).write_text(
        "from wgsextract_cli.info import VALUE\n"
        "def lookup(value=VALUE):\n    return value\n"
    )
    tests = [f"tests/test_client_{n}.py" for n in range(6)]
    (tmp_path / "tests").mkdir()
    for path in tests:
        (tmp_path / path).write_text("def test_lookup():\n    pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Existing observed interface")
    target.write_text("VALUE = 2\n" if default else "")
    _git(tmp_path, "commit", "-am", "Change interface")
    candidates, _, _ = compute(
        tmp_path,
        impact_diff(tmp_path, "HEAD^1", "HEAD"),
        set(tests),
        {
            "functions": {(caller if default else src): {"lookup": tests}},
            "modules": {src: tests, caller: tests},
            "observed_tests": tests,
        },
    )
    assert candidates["symbol"] == candidates["widely_called"] == tests


def test_xunit_collection_failure_is_attributed_to_its_module(tmp_path: Path) -> None:
    from scripts.ci_impact_diff import impact_diff
    from scripts.ci_impact_shadow import compute

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "pytest.ini").touch()
    (tmp_path / "tests").mkdir()
    path = "tests/test_broken.py"
    (tmp_path / path).write_text("def test_working():\n    pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Working test")
    (tmp_path / path).write_text("def invalid syntax\n")
    _git(tmp_path, "commit", "-am", "Broken test")
    output = tmp_path / "out"
    output.mkdir()
    report = output / "pytest.xml"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp=out/pytest",
            f"--junitxml={report}",
            "tests",
        ],
        cwd=tmp_path,
        capture_output=True,
    )
    assert run.returncode == 2
    results, unmapped = file_results(report, {path})
    assert unmapped == 0
    assert results[path]["failed"] == 1
    candidates, _, unmatched = compute(
        tmp_path, impact_diff(tmp_path, "HEAD^1", "HEAD"), {path}, {}
    )
    assert unmatched == [path]
    assert all(path in files for files in candidates.values())
