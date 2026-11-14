"""Writer status trust and conservative generic classification regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_certifications import certifications
from scripts.ci_certify import host_platform, validate_head
from scripts.ci_test_tiers import generic_test


def attestations(
    *,
    fork: bool = False,
    state: str = "success",
    stale: bool = False,
    permission: str | None = "write",
    failed_latest: bool = False,
) -> set[str]:
    repository = "WGSExtract/wgsextract-cli"
    event = {
        "pull_request": {
            "head": {
                "repo": {"full_name": "outsider/fork" if fork else repository},
                "sha": "abc",
            },
        },
    }

    def api(endpoint: str) -> Any:
        if "/permission" in endpoint:
            if permission is None:
                raise subprocess.CalledProcessError(403, ["gh"])
            return {"permission": permission}
        if "/statuses?" in endpoint:
            status = {
                "context": "local-ci/linux-x64",
                "state": state,
                "url": f"https://api.github.com/repos/{repository}/statuses/abc",
                "creator": {"login": "certifier"},
            }
            return (
                [{**status, "state": "failure"}, status] if failed_latest else [status]
            )
        return {"sha": "old" if stale else "abc"}

    return certifications(repository, event, api)


@pytest.mark.parametrize("permission", ["write", "maintain", "admin"])
def test_923_writer_can_certify_only_host_platform(permission: str) -> None:
    assert attestations(permission=permission) == {"linux-x64"}


@pytest.mark.parametrize(
    "changes",
    [
        {"fork": True},
        {"permission": "read"},
        {"stale": True},
        {"state": "failure"},
        {"permission": None},
        {"failed_latest": True},
    ],
)
def test_923_fork_outsider_stale_failed_or_unverifiable_never_skip(
    changes: dict[str, Any],
) -> None:
    assert attestations(**changes) == set()


def test_923_merge_group_never_uses_certifications() -> None:
    assert certifications("owner/repo", {"merge_group": {"head_sha": "abc"}}) == set()


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\ndef test_run(): subprocess.run(['true'])",
        "import platform\ndef test_os(): assert platform.system()",
        "def test_permissions(tmp_path): tmp_path.chmod(0o600)",
        "def test_link(tmp_path): tmp_path.symlink_to('a')",
        "def test_lines(): assert '\\r\\n' != '\\n'",
        "def test_signal(): import signal",
        "def test_unknown(shell_fixture): assert shell_fixture",
        "def test_hidden(request): request.getfixturevalue('fixture')",
        "from tests.helpers import data\ndef test_data(): assert data",
    ],
)
def test_923_sensitive_or_uncertain_tests_remain_multiplatform(
    tmp_path: Path,
    source: str,
) -> None:
    path = tmp_path / "test_example.py"
    path.write_text(source)
    assert not generic_test(path)


def test_923_pure_parsing_test_runs_once(tmp_path: Path) -> None:
    path = tmp_path / "test_example.py"
    path.write_text("import json\ndef test_parse(): assert json.loads('1') == 1")
    assert generic_test(path)


def test_923_dirty_checkout_refuses_before_any_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    (tmp_path / "dirty").write_text("untracked")
    with pytest.raises(ValueError, match="dirty"):
        validate_head({}, "abc", "owner/repo")


def test_923_stale_pr_head_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    pr = {
        "state": "open",
        "head": {"repo": {"full_name": "owner/repo"}, "sha": "old"},
    }
    with pytest.raises(ValueError, match="pushed"):
        validate_head(pr, "abc", "owner/repo")


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Linux", "x86_64", "linux-x64"),
        ("Linux", "aarch64", "linux-arm64"),
        ("Darwin", "arm64", "macos-arm64"),
        ("Darwin", "x86_64", "macos-x64"),
        ("Windows", "AMD64", "windows-x64"),
    ],
)
def test_923_status_platform_is_detected_not_claimed(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    expected: str,
) -> None:
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.machine", lambda: machine)
    assert host_platform() == expected
