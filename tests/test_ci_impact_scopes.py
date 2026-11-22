"""Scope regressions for the decisive selector pilot (#1339)."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

from coverage import Coverage

from scripts.ci_impact_diff import git_blob, impact_diff
from scripts.ci_impact_observations import read_observations
from scripts.ci_impact_scopes import changed_scopes, module_scopes


def test_nested_edit_does_not_select_the_enclosing_function() -> None:
    before = b"""\
def outer():
    def inner():
        return 1
    return inner()

def other():
    return 3
"""
    after = b"# Insert lines before the definitions.\n\n" + before.replace(
        b"        return 1", b"        return 2"
    )

    changed = changed_scopes(module_scopes(before), module_scopes(after))

    assert not changed.initialization
    assert changed.functions == {"outer.<locals>.inner"}


def test_changed_default_is_not_just_a_function_body_edit() -> None:
    before = b"def transform(value=1):\n    return value * 2\n"
    after = before.replace(b"value=1", b"value=3")

    changed = changed_scopes(module_scopes(before), module_scopes(after))

    assert changed.initialization
    assert not changed.functions


def test_conditional_definitions_are_not_assumed_to_have_one_body() -> None:
    before = b"""\
if enabled:
    def transform():
        return 1
else:
    def transform():
        return 2
"""
    after = before.replace(b"return 1", b"return 3")

    changed = changed_scopes(module_scopes(before), module_scopes(after))

    assert changed.initialization
    assert not changed.functions


def test_plain_new_function_selects_callers_not_all_module_users() -> None:
    before = b"def previous():\n    return 1\n"
    after = before + b"\ndef new():\n    return 2\n"

    changed = changed_scopes(module_scopes(before), module_scopes(after))

    assert not changed.initialization
    assert changed.functions == {"new"}


def test_repeated_function_execution_records_both_contexts(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text(
        "def run():\n"
        "    def nested():\n"
        "        return 1\n"
        "    return nested\n"
        "\n"
        "def inline(): return 3\n",
        encoding="utf-8",
    )
    traced = Coverage(config_file=False, data_file=None, include=[str(source)])
    traced.set_option("run:core", "ctrace")
    with traced.collect():
        traced.switch_context("tests/test_first.py::test_first[one|two]|setup")
        outer = cast("Callable[[], Callable[[], int]]", run_path(str(source))["run"])
        inner = outer()
        assert inner() == 1
        traced.switch_context("tests/test_second.py::test_second|run")
        assert inner() == 1
    observed = read_observations(
        traced.get_data(),
        tmp_path,
        {"module.py", "tests/test_first.py", "tests/test_second.py"},
    )

    assert observed["functions"]["module.py"] == {
        "run": ["tests/test_first.py"],
        "run.<locals>.nested": ["tests/test_first.py", "tests/test_second.py"],
    }


def test_diff_preserves_deleted_and_renamed_inputs_across_diverged_branches(
    tmp_path: Path,
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "CI test")
    moved = "old path.py"
    removed = "removed.py"
    original = b"def moved():\n    return 123\n"
    (tmp_path / moved).write_bytes(original)
    (tmp_path / removed).write_text("REMOVED = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "merge base")
    base = git("rev-parse", "HEAD")
    git("switch", "--quiet", "-c", "topic")
    git("mv", moved, "new path.py")
    git("rm", "--quiet", removed)
    git("commit", "-qm", "move and remove")
    topic = git("rev-parse", "HEAD")
    git("switch", "--quiet", "main")
    (tmp_path / moved).write_bytes(b"OTHER_BRANCH = True\n")
    git("commit", "-qam", "edit on main")

    diff = impact_diff(tmp_path, "main", "topic")

    assert diff.base == base
    assert diff.head == topic
    assert {(change.before, change.after) for change in diff.changes} == {
        (moved, "new path.py"),
        (removed, None),
    }
    assert git_blob(tmp_path, diff.base, moved) == original
