"""The parsers generated from ``wgsextract_cli.api`` Params match the
hand-written ones they replaced: same options, help text and parse results."""

import functools
from typing import Any

import pytest
from cli_snapshot import normalise_funcs, read_snapshot, snapshot

_GOLDEN = {command: normalise_funcs(data) for command, data in read_snapshot().items()}


@functools.cache
def _current() -> dict[str, Any]:
    # Built on first use, inside a test, so the user's config is isolated.
    return {command: normalise_funcs(data) for command, data in snapshot().items()}


def test_snapshot_cannot_silently_drop_other_branch_cases() -> None:
    current = _current()
    assert current.keys() == _GOLDEN.keys(), (
        "CLI command files differ; run 'just regen'"
    )
    stale = sorted(
        command for command in current if current[command] != _GOLDEN[command]
    )
    assert not stale, f"CLI snapshot differs for {stale}; run 'just regen'"


@pytest.mark.parametrize("command", sorted(_GOLDEN))
def test_parser_structure_unchanged(command: str) -> None:
    assert _current()[command]["parser"] == _GOLDEN[command]["parser"]


@pytest.mark.parametrize("command", sorted(_GOLDEN))
def test_help_text_unchanged(command: str) -> None:
    assert _current()[command]["help"] == _GOLDEN[command]["help"]


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        (command, argv)
        for command, data in sorted(_GOLDEN.items())
        for argv in data["namespaces"]
    ],
)
def test_parsed_namespace_unchanged(command: str, argv: str) -> None:
    assert (
        _current()[command]["namespaces"][argv] == _GOLDEN[command]["namespaces"][argv]
    )


def test_snapshot_regeneration_does_not_commit_user_settings() -> None:
    from wgsextract_cli.core.config import reload_settings, save_config, settings

    before = snapshot()
    save_config({"cpu_threads": 7})
    reload_settings()
    assert settings["cpu_threads"] == 7
    assert snapshot() == before
    assert settings["cpu_threads"] == 7
