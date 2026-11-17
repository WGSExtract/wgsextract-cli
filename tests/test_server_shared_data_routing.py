"""Affected-test routing contracts for the server shared-data worker guard."""

import pytest

from scripts.ci_select_tests import select_tests


def test_826_scheduler_guard_changes_select_consumer_routing_checks() -> None:
    selected = select_tests(["tests/test_server_shared_data.py"])
    assert selected == "all" or "tests/test_server_shared_data_routing.py" in selected


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_server_jobs.py",
        "tests/test_server_lifecycle.py",
        "tests/test_server_mcp.py",
    ],
)
def test_826_server_fixture_consumers_select_the_shared_data_scheduler_guard(
    path: str,
) -> None:
    selected = select_tests([path])
    assert selected == "all" or "tests/test_server_shared_data.py" in selected
