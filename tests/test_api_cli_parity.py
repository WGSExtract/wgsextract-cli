"""The parsers generated from ``wgsextract_cli.api`` Params match the
hand-written ones they replaced: same options, help text and parse results."""

import functools
import json
from typing import Any

import pytest
from cli_snapshot import GOLDEN, normalise_funcs, snapshot

_GOLDEN = normalise_funcs(json.loads(GOLDEN.read_text()))


@functools.cache
def _current() -> dict[str, Any]:
    # Built on first use, inside a test, so the user's config is isolated.
    return normalise_funcs(snapshot())


def test_snapshot_cannot_silently_drop_other_branch_cases() -> None:
    assert _current() == _GOLDEN, "CLI snapshot is stale; run 'just regen'"


@pytest.mark.parametrize("command", sorted(_GOLDEN["commands"]))
def test_parser_structure_unchanged(command: str) -> None:
    assert (
        _current()["commands"][command]["parser"]
        == (_GOLDEN["commands"][command]["parser"])
    )


@pytest.mark.parametrize("command", sorted(_GOLDEN["commands"]))
def test_help_text_unchanged(command: str) -> None:
    assert (
        _current()["commands"][command]["help"] == _GOLDEN["commands"][command]["help"]
    )


@pytest.mark.parametrize("argv", sorted(_GOLDEN["namespaces"]))
def test_parsed_namespace_unchanged(argv: str) -> None:
    assert _current()["namespaces"][argv] == _GOLDEN["namespaces"][argv]


def test_snapshot_regeneration_does_not_commit_user_settings() -> None:
    from wgsextract_cli.core.config import reload_settings, save_config, settings

    before = snapshot()
    save_config({"cpu_threads": 7})
    reload_settings()
    assert settings["cpu_threads"] == 7
    assert snapshot() == before
    assert settings["cpu_threads"] == 7
