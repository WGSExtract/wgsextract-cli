"""Unattended app updates and reboot recovery, with real Git and an OS boundary."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_mockup_review_server import SCRIPT, git

TEST_DOMAIN = "user/1234"


def git_text(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def source_repo(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.org")
    git(source, "config", "user.name", "Reviewer")
    (source / "README.md").write_text("initial")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    return source


def test_installer_requires_an_explicit_review_authority() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT.parent / "hosting/install.py")],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--allow-host" in result.stderr


def test_review_push_target_defaults_to_review_account_home(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT.parent / "hosting/local_repo.py")],
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert (tmp_path / "mockup-server/local.git/hooks/post-receive").is_file()


def test_installer_writes_only_configured_review_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent / "hosting"))
    from install import install

    run = subprocess.run

    def launchctl_stub(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[Any]:
        if args[0] == "/bin/launchctl":
            return subprocess.CompletedProcess(args, 0)
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", launchctl_stub)
    agents = tmp_path / "agents"
    hosts = ["review-host.example:8765", "10.0.0.1:8765"]
    install(tmp_path, agents, TEST_DOMAIN, hosts)
    with (agents / "org.wgsextract.mockups.plist").open("rb") as stream:
        args = plistlib.load(stream)["ProgramArguments"]
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "--allow-host"] == hosts


def test_776_updater_switches_to_existing_main_after_feature_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent / "hosting"))
    import update

    source = source_repo(tmp_path)
    git(source, "checkout", "-b", "feature")
    (source / "README.md").write_text("feature")
    git(source, "add", ".")
    git(source, "commit", "-m", "feature")
    git(tmp_path, "clone", "--branch", "feature", str(source), str(tmp_path / "app"))
    app = tmp_path / "app"
    git(app, "branch", "main", "origin/main")
    git(source, "checkout", "main")
    git(source, "merge", "--ff-only", "feature")
    running = tmp_path / "running-version"

    def restart(domain: str) -> None:
        running.write_text(git_text(app, "rev-parse", "HEAD"))

    monkeypatch.setattr(update, "restart", restart)
    update.update(tmp_path, TEST_DOMAIN)
    assert git_text(app, "branch", "--show-current") == "main"
    assert running.read_text() == git_text(source, "rev-parse", "main")


def test_776_failed_restart_retries_even_after_checkout_has_advanced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent / "hosting"))
    import update

    source = source_repo(tmp_path)
    app = tmp_path / "app"
    git(tmp_path, "clone", str(source), str(app))
    old = git_text(app, "rev-parse", "HEAD")
    running = tmp_path / "running-version"
    running.write_text(old)
    (tmp_path / "app-restarted.sha").write_text(old)
    (source / "README.md").write_text("new version")
    git(source, "add", ".")
    git(source, "commit", "-m", "new version")
    failed = False

    def restart(domain: str) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("launchd temporarily unavailable")
        running.write_text(git_text(app, "rev-parse", "HEAD"))

    monkeypatch.setattr(update, "restart", restart)
    with pytest.raises(OSError):
        update.update(tmp_path, TEST_DOMAIN)
    assert running.read_text() == old
    assert git_text(app, "rev-parse", "HEAD") != old
    update.update(tmp_path, TEST_DOMAIN)
    assert running.read_text() == git_text(source, "rev-parse", "main")


def test_776_recovery_registers_server_backup_and_updater_without_running_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent / "hosting"))
    import recover

    registered = {"org.wgsextract.mockups"}
    executions: list[str] = []

    def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "print":
            status = 0 if args[1].split("/")[-1] in registered else 1
        elif args[0] == "bootstrap":
            registered.add(Path(args[2]).stem)
            status = 0
        elif args[0] == "kickstart":
            executions.append(args[-1].split("/")[-1])
            status = 0
        else:
            raise AssertionError("unexpected OS operation")
        return subprocess.CompletedProcess(args, status, b"", b"")

    monkeypatch.setattr(recover, "launchctl", launchctl)
    recover.recover(tmp_path, TEST_DOMAIN)
    assert registered == {
        "org.wgsextract.mockups",
        "org.wgsextract.mockups.backup",
        "org.wgsextract.mockups.update",
    }
    assert "org.wgsextract.mockups" in executions
    assert "org.wgsextract.mockups.backup" not in executions
