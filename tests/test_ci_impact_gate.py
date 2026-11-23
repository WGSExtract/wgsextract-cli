"""Regression coverage for Linux symbol activation and nightly attribution."""

import gzip
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.ci_impact_backstop import failing_targets, first_bad, verified_omission
from scripts.ci_impact_gate import plan
from scripts.ci_impact_observations import SCHEMA


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/wgsextract_cli/core/writer.py", {"tests/test_text_newlines.py"}),
        (
            "src/wgsextract_cli/commands/vcf/pharmcat.py",
            {"tests/test_command_needs.py"},
        ),
        (
            "src/wgsextract_cli/core/chunked_alignment.py",
            {"tests/test_chunked_realign.py"},
        ),
        (
            "src/wgsextract_cli/commands/qc/_relate_vcf.py",
            {"tests/test_somalier_relate.py"},
        ),
        ("tests/conftest.py", set()),
    ],
    ids=["891-writer", "879-pharmcat", "1278-chunked", "1261-somalier", "root-fixture"],
)
def test_symbol_gate_preserves_historical_contracts_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, expected: set[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", *args], text=True
        ).strip()

    git("init", "-qb", "main")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    test_files = expected | {"tests/test_legacy.py"}
    Path("tests").mkdir()
    for test in test_files:
        Path(test).write_text("def test_behavior():\n    pass\n")
    source = Path(path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def write(path):\n    path.write_text('before\\n')\n")
    Path("pixi.lock").write_bytes(b"lock")
    git("add", ".")
    git("commit", "-qm", "Base")
    base = git("rev-parse", "HEAD")
    source.write_text("def write(path):\n    path.write_text('after')\n")
    git("commit", "-qam", "Change writer behavior")
    Path("out").mkdir()
    map_path = Path("out/map.json.gz")
    legacy = '["tests/test_legacy.py"]'
    recorded = datetime.now(timezone.utc)

    def set_age(hours: int) -> None:
        with gzip.open(map_path, "wt", encoding="utf-8") as stream:
            json.dump(
                {
                    "manifest": {
                        "source_sha": base,
                        "schema": SCHEMA,
                        "platform": "Linux",
                        "machine": "x86_64",
                        "python": platform.python_version(),
                        "profile": "ubuntu-x64/all/skip-independent=false",
                        "run_id": 1,
                        "attempt": 1,
                        "lock_sha256": hashlib.sha256(b"lock").hexdigest(),
                        "recorded_at": (recorded - timedelta(hours=hours)).isoformat(),
                    },
                    "observed_tests": sorted(expected or test_files),
                    "modules": {path: sorted(expected)},
                    "functions": {path: {"write": sorted(expected)}},
                    "symbol_reads": {},
                    "seconds": {},
                },
                stream,
            )

    def run_gate(
        *, event: str = "pull_request", disabled: bool = False
    ) -> dict[str, str | list[str]]:
        return plan(
            tmp_path,
            event=event,
            base=base,
            observations=map_path,
            legacy=legacy,
            disabled=disabled,
        )

    assert run_gate()["mode"] == "legacy-observation-gap"
    set_age(49)
    assert run_gate() == {
        "mode": "legacy-stale-map",
        "selection": ["tests/test_legacy.py"],
    }
    set_age(1)
    assert run_gate(disabled=True)["selection"] == ["tests/test_legacy.py"]
    assert run_gate(event="merge_group")["selection"] == ["tests/test_legacy.py"]
    if expected:
        selected = run_gate()
        assert selected["mode"] == "symbol"
        assert expected <= set(selected["selection"])
        assert "tests/test_legacy.py" not in selected["selection"]
        if path.endswith("/writer.py"):
            git("switch", "--detach", base)
            newer_test = "tests/test_new_since_map.py"
            Path(newer_test).write_text("def test_new_caller():\n    pass\n")
            git("add", newer_test)
            git("commit", "-qm", "Add a new caller after the nightly map")
            newer_base = git("rev-parse", "HEAD")
            source.write_text("def write(path):\n    path.write_text('after')\n")
            git("commit", "-qam", "Edit writer against newer base")
            checked = plan(
                tmp_path,
                event="pull_request",
                base=newer_base,
                observations=map_path,
                legacy=legacy,
                disabled=False,
            )
            assert newer_test in checked["selection"]
    else:
        assert run_gate() == {"mode": "universal-full", "selection": "all"}


def test_backstop_requires_reproduction_after_a_clean_boundary() -> None:
    outcomes = iter(["pass", "fail", "pass"])
    assert first_bad(["one", "two", "three"], lambda _: next(outcomes))[0] is None
    outcomes = iter(["pass", "fail", "fail"])
    assert first_bad(["one", "two", "three"], lambda _: next(outcomes)) == (
        "two",
        "reproduced",
    )
    outcomes = iter(["unknown", "fail", "fail"])
    assert first_bad(["one", "two", "three"], lambda _: next(outcomes))[0] is None


def test_backstop_replays_only_the_failed_parameter_and_collection_module(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        """<testsuite>
        <testcase classname="tests.test_writer.TestWriter" name="test_write[good]" />
        <testcase classname="tests.test_writer.TestWriter" name="test_write[bad]"><failure /></testcase>
        <testcase classname="" name="tests.test_broken"><error message="collection failure" /></testcase>
        </testsuite>"""
    )
    assert failing_targets(junit, {"tests/test_writer.py", "tests/test_broken.py"}) == {
        "tests/test_writer.py": {
            "tests/test_writer.py::TestWriter::test_write[bad]": "failure"
        },
        "tests/test_broken.py": {"tests/test_broken.py": "error collection failure"},
    }


def test_backstop_requires_a_real_omission_on_identical_pr_tree() -> None:
    path = "tests/test_text_newlines.py"
    receipt = {
        "schema": 1,
        "run_id": 4,
        "pr_head": "pr-head",
        "tested_git_tree": "tested-tree",
        "run_outcome": "success",
        "gate_mode": "symbol",
        "actual_selected": [path],
        "effective_selected": [],
        "variants": {"symbol": {"omitted": [path]}},
    }
    assert verified_omission(receipt, "tested-tree", "pr-head", path, 4)
    assert not verified_omission(receipt, "different-tree", "pr-head", path, 4)
    assert not verified_omission(
        receipt | {"gate_mode": "legacy"}, "tested-tree", "pr-head", path, 4
    )
    assert not verified_omission(
        receipt | {"effective_selected": [path]}, "tested-tree", "pr-head", path, 4
    )
