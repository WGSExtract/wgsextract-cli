"""Regression coverage for Linux symbol activation and nightly attribution."""

import gzip
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.ci_github import GitHub
from scripts.ci_impact_backstop import (
    failing_targets,
    first_bad,
    green_nightly,
    investigate,
    line_detection,
    replay,
    verified_omission,
    write_line_recall,
)
from scripts.ci_impact_gate import plan
from scripts.ci_impact_observations import SCHEMA
from scripts.ci_impact_shadow import GateDecision


@pytest.mark.parametrize("full_dispatch", [False, True])
def test_1464_replay_uses_only_comparable_full_main_passes(
    tmp_path: Path, full_dispatch: bool
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.test",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-qb", "main")
    for label in ("scheduled", "manual", "failed"):
        git("commit", "--allow-empty", "-qm", label)
    head, manual, scheduled = git("rev-list", "HEAD").splitlines()
    git("checkout", "-qb", "not-main", scheduled)
    git("commit", "--allow-empty", "-qm", "untrusted")
    off_main = git("rev-parse", "HEAD")

    class RunServer(GitHub):
        def request(self, method: str, path: str, data: object = None) -> object:
            if "event=schedule&branch=main" in path:
                return {
                    "workflow_runs": [
                        {"id": 10, "head_sha": scheduled, "event": "schedule"}
                    ]
                }
            if "event=workflow_dispatch&branch=main" in path:
                return {
                    "workflow_runs": [
                        {"id": 30, "head_sha": off_main, "event": "workflow_dispatch"},
                        {"id": 20, "head_sha": manual, "event": "workflow_dispatch"},
                    ]
                }
            if path.endswith("/jobs?filter=latest&per_page=100&page=1"):
                return {
                    "jobs": [
                        {
                            "name": "test-ubuntu / all",
                            "steps": [
                                {"name": "Run Tests", "conclusion": "success"},
                                {
                                    "name": "Export Linux test observations",
                                    "conclusion": "success"
                                    if full_dispatch or "/30/" in path
                                    else "skipped",
                                },
                            ],
                        }
                    ]
                }
            raise AssertionError(path)

    assert green_nightly(RunServer("owner/repo"), tmp_path, 40, head) == (
        manual if full_dispatch else scheduled
    )


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
        ("src/wgsextract_cli/core/__init__.py", set()),
    ],
    ids=[
        "891-writer",
        "879-pharmcat",
        "1278-chunked",
        "1261-somalier",
        "root-fixture",
        "package-initializer",
    ],
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
    if not expected:
        for name in ("test_server_paths.py", "test_realign_resume.py"):
            (Path("tests") / name).write_text("def test_behavior():\n    pass\n")
    source = Path(path)
    source.parent.mkdir(parents=True, exist_ok=True)
    initializer = path.endswith("/__init__.py")
    source.write_text(
        "VALUE = 'before'\n"
        if initializer
        else "def write(path):\n    path.write_text('before\\n')\n"
    )
    Path("pixi.lock").write_bytes(b"lock")
    git("add", ".")
    git("commit", "-qm", "Base")
    base = git("rev-parse", "HEAD")
    source.write_text(
        "VALUE = 'after'\n"
        if initializer
        else "def write(path):\n    path.write_text('after')\n"
    )
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
        *, event: str = "pull_request", disabled: bool = False, ready_tier: bool = False
    ) -> GateDecision:
        return plan(
            tmp_path,
            event=event,
            base=base,
            observations=map_path,
            legacy=legacy,
            disabled=disabled,
            ready_tier=ready_tier,
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
        assert run_gate(ready_tier=True) == {
            "mode": "universal-fast",
            "selection": [
                "tests/test_legacy.py",
                *([] if initializer else ["tests/test_realign_resume.py"]),
                "tests/test_server_paths.py",
            ],
        }


@pytest.mark.parametrize(
    ("drift", "mode"),
    [
        ("added", "symbol"),
        ("modified", "symbol"),
        ("deleted", "symbol"),
        ("no-importer", "legacy-unobserved-source-drift"),
        ("dynamic", "legacy-unobserved-source-drift"),
        ("relative-dynamic", "symbol"),
        ("relative-dynamic-unknown", "legacy-unobserved-source-drift"),
        ("builtin-fromlist", "symbol"),
        ("builtin-fromlist-unknown", "legacy-unobserved-source-drift"),
        ("unparsed", "legacy-unobserved-source-drift"),
        ("initializer", "legacy-unobserved-source-drift"),
        ("asset", "legacy-unobserved-source-drift"),
        ("harness", "legacy-test-harness-changed-since-map"),
        ("project-config", "legacy-test-harness-changed-since-map"),
        ("run-45252205057", "legacy-test-harness-changed-since-map"),
    ],
)
def test_1482_symbol_gate_resolves_importable_drift_but_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str, mode: str
) -> None:
    # Run 45252205057 tested e69ea2587 (PR #1470) against 64c2c6924,
    # with a 7f81a036c map exported by nightly run 45233258080. Model its PR
    # and the intervening native-aligner, OpenAPI and test helper changes.
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=", *args], cwd=tmp_path, text=True
        ).strip()

    def write(path: str, text: str) -> None:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def commit() -> str:
        git("add", ".")
        git("commit", "-qm", "Tree under test")
        return git("rev-parse", "HEAD")

    git("init", "-qb", "main")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    source = "src/wgsextract_cli/core"
    module = f"{source}/native_aligners.py"
    launcher = "tests/fake_tool_launchers.py"
    schema = "src/wgsextract_cli/assets/schemas/wgsextract.server.openapi.json"
    workflow = ".github/workflows/install_lab.yml"
    write(workflow, "name: before\n")
    write("pixi.lock", "lock")
    write("tests/test_github_actions_policy.py", "def test_workflows(): pass\n")
    write("tests/test_legacy.py", "def test_unrelated(): pass\n")
    caller = "tests/test_native_aligners_minimap2.py"
    dynamic_caller = "tests/test_dynamic_importer.py"
    write(caller, "from wgsextract_cli.core.backends import align\n")
    write(
        f"{source}/backends.py",
        "from .native_aligners import align\n"
        if drift not in {"added", "no-importer"}
        else "def align(): pass\n",
    )
    if drift == "no-importer":
        write(caller, 'EXPECTED_LOGGER = "wgsextract_cli.core.native_aligners"\n')
    if drift in {"relative-dynamic", "relative-dynamic-unknown"}:
        anchor = '"wgsextract_cli.core"' if drift == "relative-dynamic" else "package"
        write(
            dynamic_caller,
            "from importlib import import_module as load\n"
            "def call(package):\n"
            f'    return load(name=".native_aligners", package={anchor})\n',
        )
    if drift in {"builtin-fromlist", "builtin-fromlist-unknown"}:
        names = '["native_aligners"]' if drift == "builtin-fromlist" else "fromlist"
        write(
            dynamic_caller,
            "def call(fromlist):\n"
            f'    return __import__("wgsextract_cli.core", fromlist={names})\n',
        )
    if drift not in {"added", "no-importer"}:
        write(module, "def align(): return 'old'\n")
    if drift in {"asset", "run-45252205057"}:
        write(schema, "{}\n")
    if drift in {"harness", "run-45252205057"}:
        write(launcher, "def launch(): return 'old'\n")
    map_sha = commit()
    if drift == "deleted":
        (tmp_path / module).unlink()
    else:
        write(module, "def align(): return 'new'\n")
    if drift == "added":
        write(f"{source}/backends.py", "from .native_aligners import align\n")
    if drift == "dynamic":
        write(
            module,
            "from importlib import import_module as load\n"
            "def align(name): return load(name)\n",
        )
    if drift == "unparsed":
        write(module, "def align(!!!)\n")
    if drift == "initializer":
        write(f"{source}/__init__.py", "from .native_aligners import align\n")
    if drift in {"asset", "run-45252205057"}:
        write(schema, '{"info": {}}\n')
    if drift in {"harness", "run-45252205057"}:
        write(launcher, "def launch(): return 'new'\n")
    if drift == "project-config":
        write("pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-x'\n")
    base = commit()
    write(workflow, "name: after\n")
    commit()
    map_path = tmp_path / "map.json.gz"
    with gzip.open(map_path, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "manifest": {
                    "source_sha": map_sha,
                    "schema": SCHEMA,
                    "platform": "Linux",
                    "machine": "x86_64",
                    "python": platform.python_version(),
                    "profile": "ubuntu-x64/all/skip-independent=false",
                    "run_id": 45233258080,
                    "attempt": 1,
                    "lock_sha256": hashlib.sha256(b"lock").hexdigest(),
                },
                "observed_tests": ["tests/test_github_actions_policy.py"],
                "modules": {},
                "functions": {},
                "symbol_reads": {},
                "seconds": {},
            },
            stream,
        )
    monkeypatch.chdir(tmp_path)
    result = plan(
        tmp_path,
        event="pull_request",
        base=base,
        observations=map_path,
        legacy='["tests/test_legacy.py"]',
        disabled=False,
    )
    selected = [caller, "tests/test_github_actions_policy.py"]
    if drift in {"relative-dynamic", "builtin-fromlist"}:
        selected.append(dynamic_caller)
    assert result == {
        "mode": mode,
        "selection": sorted(selected) if mode == "symbol" else ["tests/test_legacy.py"],
    }


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


def test_symbol_gate_reports_bad_legacy_selection_without_a_traceback(
    tmp_path: Path,
) -> None:
    output = tmp_path / "decision.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.ci_impact_gate",
            "--legacy",
            "tests/test_writer.py",
            "--event",
            "pull_request",
            "--base",
            "HEAD",
            "--observations",
            str(tmp_path / "map.json.gz"),
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--legacy" in result.stderr and "JSON array" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


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


def test_replay_reuses_tools_and_runs_historical_source(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-qb", "main")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    for filename in ("pixi.lock", "pixi.toml", "pyproject.toml"):
        (tmp_path / filename).touch()
    workspace = tmp_path / "out"
    workspace.mkdir()
    test = "tests/test_local_tool.py"
    (tmp_path / "tests").mkdir()
    (tmp_path / test).write_text(
        "from pathlib import Path\n"
        "def test_tool():\n"
        "    assert Path('.pixi/envs/prepared/bin/tool').read_text() == 'ready'\n"
    )
    git("add", ".")
    git("commit", "-qm", "Use prepared local tool")
    sha = git("rev-parse", "HEAD")
    (tmp_path / test).write_text("def test_tool():\n    assert False\n")
    tool = tmp_path / ".pixi/envs/prepared/bin/tool"
    tool.parent.mkdir(parents=True)
    tool.write_text("ready")
    assert (
        replay(tmp_path, workspace, sha, test, f"{test}::test_tool", "failure")
        == "pass"
    )
    assert tool.read_text() == "ready"
    assert not (workspace / "tree").exists()
    collision = workspace / "tree"
    collision.mkdir()
    sentinel = collision / "existing.txt"
    sentinel.write_text("keep")
    assert replay(tmp_path, workspace, sha, test, f"{test}::test_tool", "") == "unknown"
    assert sentinel.read_text() == "keep"


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


def test_1452_backstop_scores_reproducible_files_only_for_the_exact_pr_receipt(
    tmp_path: Path,
) -> None:
    a, b = "tests/test_a.py", "tests/test_b.py"
    receipt = {
        "schema": 1,
        "attempt": 2,
        "run_id": 4,
        "pr_head": "pr-head",
        "tested_git_tree": "tested-tree",
        "run_outcome": "failure",
        "gate_mode": "symbol",
        "effective_selected": [a, b],
        "variants": {"line": {"status": "eligible", "selected": [a], "omitted": [b]}},
    }
    assert line_detection(receipt, "different-tree", "pr-head", a, 4, 2) is None
    assert line_detection(receipt, "tested-tree", "pr-head", a, 4, 1) is None
    assert (
        line_detection(
            receipt | {"gate_mode": "legacy"}, "tested-tree", "pr-head", a, 4, 2
        )
        is None
    )
    detections = []
    for test in (a, a, b):
        detection = line_detection(receipt, "tested-tree", "pr-head", test, 4, 2)
        assert detection is not None
        detections.append(
            {
                "test": test,
                "pr": 20,
                "pr_run": 4,
                "commit": "first-bad",
                "tested_git_tree": "tested-tree",
                **detection,
            }
        )
    output = tmp_path / "out/recall.json"
    write_line_recall(output, 100, detections)
    report = json.loads(output.read_text())["reports"][0]
    assert report["failing_files"] == [a, b]
    assert (report["line_caught"], report["symbol_caught"]) == (1, 2)
    assert report["line_missed_files"] == [b]


def test_1452_backstop_preserves_confirmed_witness_when_attribution_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.test",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-qb", "main")
    for name in ("pixi.lock", "pixi.toml", "pyproject.toml"):
        (tmp_path / name).touch()
    test = "tests/test_branch.py"
    source = tmp_path / test
    source.parent.mkdir()
    source.write_text("def test_branch():\n    assert True\n")
    git("add", ".")
    git("commit", "-qm", "Green nightly")
    baseline = git("rev-parse", "HEAD")
    source.write_text("def test_branch():\n    assert False\n")
    git("commit", "-qam", "Reproducible failure")
    head = git("rev-parse", "HEAD")

    class RunServer(GitHub):
        def request(self, method: str, path: str, data: object = None) -> object:
            if "event=schedule&branch=main" in path:
                return {
                    "workflow_runs": [
                        {"id": 10, "head_sha": baseline, "event": "schedule"}
                    ]
                }
            if "event=workflow_dispatch&branch=main" in path:
                return {"workflow_runs": []}
            if "/actions/runs/10/jobs?" in path:
                return {
                    "jobs": [
                        {
                            "name": "test-ubuntu / all",
                            "steps": [
                                {"name": "Run Tests", "conclusion": "success"},
                            ],
                        }
                    ]
                }
            if path.endswith(f"commits/{head}/pulls"):
                raise TimeoutError("attribution API timed out")
            raise AssertionError(path)

    monkeypatch.chdir(tmp_path)
    nightly = Path("out/nightly.xml")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp=out/pytest",
            f"--junitxml={nightly}",
            test,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    witness = Path("out/replay/witnesses.jsonl")
    with pytest.raises(TimeoutError, match="attribution API"):
        investigate(
            RunServer("owner/repo"),
            nightly,
            11,
            witness.with_name("report.json"),
            witnesses=witness,
        )
    assert json.loads(witness.read_text()) == [head, test]
