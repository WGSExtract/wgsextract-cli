"""Shared-data serialization contracts selected with their server-suite inputs."""

import ast

import pytest

from tests.test_ci_scheduling import REPO_ROOT
from tests.test_ci_scheduling import (
    _repo_on_subprocess_path as _repo_on_subprocess_path,
)

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    "module_path",
    [
        "tests/test_server_jobs.py",
        "tests/test_server_lifecycle.py",
        "tests/test_server_mcp.py",
    ],
)
def test_826_split_server_fixture_users_keep_shared_data_on_one_worker(
    pytester: pytest.Pytester, module_path: str
) -> None:
    source = (REPO_ROOT / module_path).read_text()
    imports = "\n".join(
        ast.get_source_segment(source, node) or ""
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    test = """
from pathlib import Path

def test_worker(request):
    with Path("workers.txt").open("a") as output:
        output.write(request.config.workerinput["workerid"] + "\\n")
"""
    pytester.makeconftest("from tests.conftest import pytest_xdist_make_scheduler")
    pytester.makepyfile(
        test_split=imports + "\n" + test,
        test_other="from tests.smoke_utils import ensure_fake_data\n" + test,
    )
    (pytester.path / "out").mkdir()
    result = pytester.runpytest_subprocess(
        "-n",
        "2",
        "--dist",
        "loadgroup",
        "-q",
        "--basetemp",
        str(pytester.path / "out" / "pytest"),
    )
    result.assert_outcomes(passed=2)
    workers = (pytester.path / "workers.txt").read_text().splitlines()
    assert len(workers) == 2 and len(set(workers)) == 1
