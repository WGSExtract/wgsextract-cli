import os
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("tier", "skip", "passed", "deselected"),
    [
        ("all", False, 3, 0),
        ("core", False, 2, 1),
        ("integration", False, 1, 2),
        ("all", True, 2, 1),
        ("core", True, 1, 2),
    ],
)
def test_761_ci_tiers_preserve_every_test_including_new_modules(
    pytester: pytest.Pytester, tier: str, skip: bool, passed: int, deselected: int
) -> None:
    pytester.makeconftest(
        """
from tests.conftest import pytest_collection_modifyitems

def pytest_addoption(parser):
    parser.addoption("--ci-tier", default="all")
    parser.addoption("--ci-skip-independent", action="store_true")
    parser.addoption("--ci-shard", default="all")
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


def test_761_core_shards_run_every_module_once_with_shared_fixtures(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest(
        """
from pathlib import Path
import pytest
from tests.conftest import pytest_collection_modifyitems

def pytest_addoption(parser):
    parser.addoption("--ci-tier", default="core")
    parser.addoption("--ci-skip-independent", action="store_true")
    parser.addoption("--ci-shard", default="all")

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
    for shard in ("0/2", "1/2"):
        result = pytester.runpytest_subprocess(
            "--ci-shard",
            shard,
            "--basetemp",
            str(pytester.path / "out" / shard.replace("/", "-")),
        )
        assert result.ret == 0
    assert sorted((pytester.path / "prepared.txt").read_text().splitlines()) == sorted(
        names
    )
    assert sorted((pytester.path / "ran.txt").read_text().splitlines()) == sorted(
        f"{name}::{case}" for name in names for case in ("first", "second")
    )


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
