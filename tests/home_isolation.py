"""Home and config isolation for pytest fixtures and subprocesses."""

import json
from pathlib import Path

import pytest


def state_directory_setting(root: Path) -> str:
    """A config.toml line keeping run records and reports under ``root``, not
    in the checkout (the default data folder)."""
    return f"state_directory = {json.dumps(str(root / 'state'))}\n"


def isolated_home_env(home: Path) -> dict[str, str]:
    """Every home/config location the CLI consults, pointed at ``home``."""
    paths = {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return {name: str(path) for name, path in paths.items()}


def isolate_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Point every home/config location the CLI consults at ``home``."""
    for name, value in isolated_home_env(home).items():
        monkeypatch.setenv(name, value)
