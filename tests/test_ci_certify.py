"""Writer status trust and conservative generic classification regressions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_certifications import certifications
from scripts.ci_certify import host_platform, run_layer, validate_head
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
            if permission == "missing-field":
                return {}
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
        {"permission": "missing-field"},
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
        "from wgsextract_cli.core.utils import run_command\ndef test_command(): run_command(['true'])",
        "import urllib.request\ndef test_network(): assert urllib.request.urlopen('x')",
        "import pytest\n@pytest.mark.parametrize('cache', [1], indirect=True)\ndef test_cache(cache): assert cache",
        "def test_cache(*, cache): assert cache",
        "from pytest import fixture as f\n@f\ndef tmp_path(): return launch_external_program()\ndef test_cache(tmp_path): assert tmp_path",
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


def test_923_unknown_autouse_fixture_cannot_hide_shell_out(tmp_path: Path) -> None:
    path = tmp_path / "test_example.py"
    path.write_text("def test_parse(): assert 1 == 1")
    (tmp_path / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\n"
        "def bootstrap(): return launch_external_program()"
    )
    assert not generic_test(path)


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


def test_923_local_fork_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    with pytest.raises(ValueError, match="fork"):
        validate_head(
            {"head": {"repo": {"full_name": "outsider/fork"}, "sha": "abc"}},
            "abc",
            "owner/repo",
        )


def test_923_clean_but_moved_local_head_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Certification regression",
            "-c",
            "user.email=certification@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Local-only regression fixture",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="Local HEAD"):
        validate_head(
            {
                "state": "open",
                "head": {"repo": {"full_name": "owner/repo"}, "sha": "old"},
            },
            "old",
            "owner/repo",
        )


def test_923_linting_the_test_runner_does_not_turn_lint_into_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _, log = run_layer(
        "generic",
        [
            [
                sys.executable,
                "-c",
                "import argparse; p=argparse.ArgumentParser(); p.add_argument('path'); "
                "p.parse_args(); print('validation completed')",
                "scripts/ci_run_tests.py",
            ]
        ],
        "regression",
        1,
    )
    assert "validation completed" in Path(log).read_text()


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
