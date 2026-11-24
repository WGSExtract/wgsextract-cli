import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from scripts.ci_impact_results import file_results
from scripts.ci_shard_receipt import combine
from scripts.ci_test_shards import Shard, artifact_durations, plan, suite_files

pytest_plugins = ["pytester"]

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _repo_on_subprocess_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The generated conftests import `tests.*`, which the pytester subprocess
    # can only resolve when the repository root is on its PYTHONPATH.
    existing = os.environ.get("PYTHONPATH")
    paths = [str(REPO_ROOT), *([existing] if existing else [])]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))


def test_shared_smoke_modules_run_on_one_xdist_worker(
    pytester: pytest.Pytester,
) -> None:
    # #515: pytest 9.2 broke xdist's loadgroup, spreading these over workers.
    pytester.makeconftest("from tests.conftest import pytest_xdist_make_scheduler")
    test = """
from pathlib import Path
from tests.smoke_utils import ensure_fake_data

def test_worker(request):
    with Path("workers.txt").open("a") as output:
        output.write(request.config.workerinput["workerid"] + "\\n")
"""
    pytester.makepyfile(test_first=test, test_second=test)
    (pytester.path / "out").mkdir()
    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "--dist",
        "loadgroup",
        "-vv",
        "--basetemp",
        str(pytester.path / "out" / "pytest"),
    )

    result.assert_outcomes(passed=2)
    workers = (pytester.path / "workers.txt").read_text().splitlines()
    assert len(workers) == 2 and len(set(workers)) == 1


def test_explicit_config_override_does_not_leak_between_tests(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest("from tests.conftest import _isolate_user_config")
    pytester.makepyfile(
        """
from wgsextract_cli.core import config

def test_first(tmp_path):
    own = tmp_path / "override.toml"
    own.write_text('reference_fasta = "/cfg/ref.fa"\\n')
    config.set_config_path(own)
    config.reload_settings()
    assert config.settings["reference_fasta"] == "/cfg/ref.fa"

def test_second():
    assert config.settings.get("reference_fasta") is None
"""
    )
    (pytester.path / "out").mkdir()
    result = pytester.runpytest_subprocess(
        "--basetemp", str(pytester.path / "out" / "pytest")
    )
    result.assert_outcomes(passed=2)


def test_1218_each_test_gets_its_own_empty_home(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("from tests.conftest import _isolate_user_config")
    pytester.makepyfile(
        """
import os
from pathlib import Path

def test_first():
    (Path(os.environ["HOME"]) / "left-behind").write_text("x")

def test_second():
    assert not (Path(os.environ["HOME"]) / "left-behind").exists()
"""
    )
    (pytester.path / "out").mkdir()
    result = pytester.runpytest_subprocess(
        "--basetemp", str(pytester.path / "out" / "pytest")
    )
    result.assert_outcomes(passed=2)


@pytest.mark.parametrize(
    ("tier", "skip", "passed", "deselected"),
    [
        ("all", False, 3, 0),
        ("core", False, 2, 1),
        ("integration", False, 1, 2),
        ("all", True, 3, 0),
        ("core", True, 2, 1),
    ],
)
def test_761_ci_tiers_preserve_every_test_including_new_modules(
    pytester: pytest.Pytester, tier: str, skip: bool, passed: int, deselected: int
) -> None:
    pytester.makeconftest(
        """
from tests.conftest import pytest_addoption, pytest_collection_modifyitems
"""
    )
    pytester.makepyfile(
        test_realign="def test_real_workflow(): pass",
        test_genotyping="def test_genotype(): pass",
        test_new_feature="def test_new_feature(): pass",
    )
    flags = ["--ci-skip-independent"] if skip else []
    result = pytester.runpytest_subprocess(
        "--ci-tier",
        tier,
        *flags,
        "--basetemp",
        str(pytester.path / "out" / "pytest"),
    )
    result.assert_outcomes(passed=passed, deselected=deselected)


def test_1492_n_way_hash_fallback_runs_each_module_fixture_once(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest(
        """
from pathlib import Path
import pytest
from tests.conftest import pytest_addoption, pytest_collection_modifyitems

@pytest.fixture(scope="module", autouse=True)
def prepared_module(request):
    with Path("prepared.txt").open("a") as out:
        out.write(request.module.__name__ + "\\n")
"""
    )
    source = """
from pathlib import Path

def test_first():
    with Path("ran.txt").open("a") as out:
        out.write(__name__ + "::first\\n")

def test_second():
    with Path("ran.txt").open("a") as out:
        out.write(__name__ + "::second\\n")
"""
    names = ("test_new_feature", "test_sample", "test_other_feature", "test_another")
    pytester.makepyfile(**dict.fromkeys(names, source))
    for shard in ("0/3", "1/3", "2/3"):
        result = pytester.runpytest_subprocess(
            "--ci-tier",
            "core",
            "--ci-shard",
            shard,
            "--basetemp",
            str(pytester.path / "out" / shard.replace("/", "-")),
        )
        assert result.ret in {0, pytest.ExitCode.NO_TESTS_COLLECTED}
    assert sorted((pytester.path / "prepared.txt").read_text().splitlines()) == sorted(
        names
    )
    assert sorted((pytester.path / "ran.txt").read_text().splitlines()) == sorted(
        f"{name}::{case}" for name in names for case in ("first", "second")
    )


def test_1492_balance_three_jobs_without_splitting_shared_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    test_dir = Path("tests")
    test_dir.mkdir()
    files = [f"tests/test_shard_example_{i}.py" for i in range(25)]
    for path in files:
        Path(path).touch()
    for path in files[:2]:
        Path(path).write_text(
            "from tests.smoke_utils import ensure_fake_data\n", encoding="utf-8"
        )
    durations = dict.fromkeys(files[:-1], 1200)
    durations.update({files[0]: 700, files[1]: 500})
    selection = json.dumps(files)
    shards = plan("ubuntu", selection, durations, skip_independent=False)
    contents = [set(json.loads(shard["selection"])) for shard in shards]
    assert len(shards) == 3
    assert len(set.union(*contents)) == sum(map(len, contents)) == len(files)
    assert any(set(files[:2]) <= group for group in contents)
    times = [shard["seconds"] for shard in shards]
    assert max(times) <= 1.2 * sum(times) / len(times)
    assert shards == plan(
        "ubuntu", json.dumps(files[::-1]), durations, skip_independent=False
    )
    for timing in (durations, {}):
        assert (
            len(plan("ubuntu", json.dumps(files[:2]), timing, skip_independent=False))
            == 1
        )
    one_file = json.dumps([files[0]])
    [small] = plan("ubuntu", one_file, durations, skip_independent=False, gate="symbol")
    assert json.loads(small["selection"]) == [files[0]]
    assert small["gate"] == "symbol"


def test_1492_partial_nightly_artifacts_do_not_seed_shard_estimates() -> None:
    def report(module: str, duration: float) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("shard-count", "2")
            archive.writestr(
                "pytest.xml",
                f'<testsuite><testcase classname="tests.{module}" name="test_case" '
                f'time="{duration}"/></testsuite>',
            )
        return stream.getvalue()

    evidence = {"all": report("test_first", 4), "shard-2": report("test_second", 9)}
    files = {"tests/test_first.py", "tests/test_second.py"}
    assert artifact_durations(evidence, files) == {
        "tests/test_first.py": 4,
        "tests/test_second.py": 9,
    }
    with pytest.raises(ValueError, match="incomplete timing run"):
        artifact_durations({"all": evidence["all"]}, files)


def test_1492_cross_module_xdist_group_stays_on_one_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("tests").mkdir()
    files = [f"tests/test_worker_{index}.py" for index in range(9)]
    for path in files:
        Path(path).touch()
    for path in files[:2]:
        Path(path).write_text(
            'import pytest\npytestmark = pytest.mark.xdist_group("fixture")\n',
            encoding="utf-8",
        )
    durations = dict.fromkeys(files, 350)
    durations.update({files[0]: 175, files[1]: 175})
    shards = plan("windows", json.dumps(files), durations, skip_independent=False)
    assert len(shards) == 2
    assert any(
        set(files[:2]) <= set(json.loads(shard["selection"])) for shard in shards
    )


def test_1492_does_not_split_modules_already_running_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("tests").mkdir()
    files = ["tests/test_slow_a.py", "tests/test_slow_b.py"]
    for path in files:
        Path(path).touch()
    [shard] = plan(
        "ubuntu",
        json.dumps(files),
        dict(zip(files, [2500, 2300], strict=True)),
        skip_independent=False,
    )
    assert shard["seconds"] == 2500


def test_1492_imputation_shard_owns_tool_setup_and_suffix_tests_are_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("tests").mkdir()
    files = [
        "tests/test_imputation.py",
        "tests/workflow_test.py",
        "tests/test_helpers.py",
    ]
    for path in files:
        Path(path).touch()
    shards = plan("macos", "all", dict.fromkeys(files, 3000), skip_independent=False)
    assert set(suite_files()) == set(files)
    assert len(shards) == 1  # Two bins cannot meet the imbalance limit.
    for index in range(9):
        path = f"tests/test_more_{index}.py"
        Path(path).touch()
        files.append(path)
    shards = plan("macos", "all", dict.fromkeys(files, 900), skip_independent=False)
    assert len(shards) == 2
    assert "tests/test_imputation.py" in json.loads(shards[0]["selection"])
    assert any(
        "tests/workflow_test.py" in json.loads(shard["selection"]) for shard in shards
    )


def test_1492_sharded_linux_shadow_keeps_nonlead_failures(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    output = tmp_path / "shadow"
    for label, module, status in (
        ("all", "test_first", 0),
        ("shard-2", "test_second", 1),
    ):
        shard_dir = reports / f"tests-ubuntu-{label}"
        shard_dir.mkdir(parents=True)
        (shard_dir / "shard-exit-status").write_text(f"{status}\n", encoding="utf-8")
        failure = '<failure message="regression"/>' if status else ""
        (shard_dir / "pytest.xml").write_text(
            f'<testsuites><testsuite><testcase classname="tests.{module}" '
            f'name="test_case" time="1">{failure}</testcase></testsuite></testsuites>',
            encoding="utf-8",
        )
    shards: list[Shard] = [
        {
            "label": "all",
            "selection": '["tests/test_first.py"]',
            "gate": "universal-full",
            "seconds": 1,
        },
        {
            "label": "shard-2",
            "selection": '["tests/test_second.py"]',
            "gate": "universal-full",
            "seconds": 1,
        },
    ]
    combine(shards, reports, output)
    observed, unmapped = file_results(
        output / "pytest.xml", {"tests/test_first.py", "tests/test_second.py"}
    )
    assert unmapped == 0
    assert observed["tests/test_first.py"]["passed"] == 1
    assert observed["tests/test_second.py"]["failed"] == 1
    assert (output / "outcome").read_text().strip() == "failure"
    decision = json.loads((output / "gate.json").read_text())
    assert decision == {
        "mode": "universal-full",
        "selection": ["tests/test_first.py", "tests/test_second.py"],
    }
    rerun = reports / "tests-ubuntu-shard-2"
    (rerun / "shard-exit-status").write_text("0\n", encoding="utf-8")
    (rerun / "pytest.xml").write_text(
        '<testsuite><testcase classname="tests.test_second" name="test_case" time="1"/></testsuite>',
        encoding="utf-8",
    )
    combine(shards, reports, output)
    observed, _ = file_results(output / "pytest.xml", set(observed))
    assert observed["tests/test_first.py"]["passed"] == 1
    assert observed["tests/test_second.py"]["passed"] == 1
    assert (output / "outcome").read_text().strip() == "success"


@pytest.mark.parametrize("required", [False, True])
def test_761_benchmark_smoke_cannot_silently_skip_missing_real_inputs(
    pytester: pytest.Pytester, required: bool
) -> None:
    pytester.makeconftest(
        """
from pathlib import Path
from tests.conftest import pytest_addoption
import tests.test_smoke_real_full as smoke

# OS boundary: this isolated checkout has no benchmark or configured inputs.
smoke.REPO_ROOT = Path.cwd()
real_dataset = smoke.real_dataset
"""
    )
    pytester.makepyfile("def test_real_input(real_dataset): pass")
    flags = ["--ci-require-real-data"] if required else []
    result = pytester.runpytest_subprocess(
        *flags, "--basetemp", str(pytester.path / "out" / "pytest")
    )
    result.assert_outcomes(errors=int(required), skipped=int(not required))
