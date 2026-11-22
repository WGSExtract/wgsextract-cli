"""Regressions for the #1339 advisory Linux report."""

from __future__ import annotations

import gzip
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from scripts.ci_impact_observations import SCHEMA
from scripts.ci_impact_results import file_results

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_shadow_reports_source_scans_and_base_only_failures(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    cmd_path = "src/wgsextract_cli/commands/writer.py"
    fixture_files = {
        cmd_path: "def write(path):\n    path.write_text('ok\\n')\n",
        "pixi.lock": "lock\n",
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
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["tested_base"] == base
    assert report["pr_head"] == pr_head
    assert report["comparable"]
    assert "tests/test_unrun.py" not in report["actual_results"]
    assert len(report["variants"]) == 4
    for variant in report["variants"].values():
        assert {"tests/test_text_newlines.py", "tests/test_command_needs.py"} <= set(
            variant["selected"]
        )
        assert variant["observed_omitted_failures"] == ["tests/test_qualify_30x.py"]
        assert variant["estimated_test_minutes"] == 0.1
    assert "not proof of PR origin" in (output / "summary.md").read_text()


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


def test_symbol_keeps_caller_when_import_binding_removed(tmp_path: Path) -> None:
    from scripts.ci_impact_diff import impact_diff
    from scripts.ci_impact_shadow import compute

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.test")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    src = "src/wgsextract_cli/info.py"
    target = tmp_path / src
    target.parent.mkdir(parents=True)
    target.write_text("import sys\n\ndef report():\n    return sys.platform\n")
    tested = {"tests/test_report.py", "tests/test_other_api.py"}
    for test in tested:
        path = tmp_path / test
        path.parent.mkdir(exist_ok=True)
        path.write_text("def test_contract():\n    pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Existing observed report")
    target.write_text("def report():\n    return sys.platform\n")
    _git(tmp_path, "commit", "-am", "Remove used import")

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
    assert candidates["symbol"] == ["tests/test_report.py"]
    assert candidates["widely_called"] == ["tests/test_report.py"]


def test_xunit_collection_failure_is_attributed_to_its_module(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").touch()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_broken.py").write_text("def invalid syntax\n")
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
    results, unmapped = file_results(report, {"tests/test_broken.py"})
    assert unmapped == 0
    assert results["tests/test_broken.py"]["failed"] == 1
