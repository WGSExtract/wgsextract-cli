"""Writer status trust and conservative generic classification regressions."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_certifications import certifications, publish_certification
from scripts.ci_certify import host_platform, run_layer, validate_head
from scripts.ci_certify_inputs import (
    check_packages,
    validate_bootstrap,
)
from scripts.ci_test_tiers import generic_test
from scripts.pr_checks_ready import checks_ready


def attestations(
    *,
    fork: bool = False,
    state: str = "success",
    stale: bool = False,
    permission: str | None = "write",
    failed_latest: bool = False,
    changed_verifier: bool = False,
    missing_verifier: bool = False,
) -> set[str]:
    repository = "WGSExtract/wgsextract-cli"
    event = {
        "pull_request": {
            "base": {"sha": "base"},
            "head": {
                "repo": {"full_name": "outsider/fork" if fork else repository},
                "sha": "abc",
            },
        },
    }

    def api(endpoint: str) -> Any:
        if "/contents/" in endpoint:
            if missing_verifier:
                raise subprocess.CalledProcessError(404, ["gh"])
            return {
                "sha": "changed"
                if changed_verifier and endpoint.endswith("ref=abc")
                else "trusted"
            }
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
        {"changed_verifier": True},
        {"missing_verifier": True},
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
        "from pathlib import Path\ndef test_path(): assert str(Path('a') / 'b') == 'a/b'",
        "def test_fixture_path(tmp_path): assert str(tmp_path / 'b').endswith('/b')",
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


@pytest.mark.parametrize("bucket", ["pass", "fail", "pending"])
def test_923_attestations_do_not_break_landing_when_ready_run_proves_coverage(
    bucket: str,
) -> None:
    url = "https://github.com/owner/repo/actions/runs/10/job/"
    checks = [
        {
            "name": name,
            "workflow": "PR area checks",
            "bucket": "pass",
            "link": url + str(index),
        }
        for index, name in enumerate(("changes", "required", "Ready PR area checks"))
    ]
    checks.append(
        {"name": "local-ci/linux-x64", "workflow": "", "bucket": bucket, "link": ""}
    )
    runs = [
        {"id": 10, "workflow_id": 1, "status": "completed", "conclusion": "success"}
    ]
    assert checks_ready(checks, run_id=10, runs=runs)
    checks[1]["bucket"] = "fail"
    assert not checks_ready(checks, run_id=10, runs=runs)
    checks[-1]["name"] = "local-ci/outsider"
    assert not checks_ready(checks, run_id=10, runs=runs)


def test_923_installed_tool_must_match_lock_url_and_digest(tmp_path: Path) -> None:
    records = tmp_path / "conda-meta"
    records.mkdir()
    package = {
        "kind": "conda",
        "name": "samtools",
        "version": "1.24",
        "build": "build",
        "url": "https://example.invalid/samtools.conda",
        "sha256": "a" * 64,
    }
    record = records / "samtools.json"
    record.write_text(json.dumps(package))
    check_packages(tmp_path, [package])
    record.write_text(json.dumps({**package, "sha256": "b" * 64}))
    with pytest.raises(ValueError, match="pixi.lock"):
        check_packages(tmp_path, [package])


def test_923_stale_or_modified_python_dependency_cannot_certify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    site = tmp_path / "Lib/site-packages"
    metadata = site / "sample-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: sample\nVersion: 1.0\n")
    module = site / "sample.py"
    module.write_bytes(b"version = '1.0'\n")
    import base64

    encoded = (
        base64.urlsafe_b64encode(hashlib.sha256(module.read_bytes()).digest())
        .decode()
        .rstrip("=")
    )
    (metadata / "RECORD").write_text(
        f"sample.py,sha256={encoded},{module.stat().st_size}\n"
    )
    wheel = tmp_path / "sample.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.write(module, "sample.py")
        archive.write(metadata / "METADATA", "sample-1.0.dist-info/METADATA")
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    retained = Path("out/certify/inputs/wheels") / f"{checksum}.whl"
    retained.parent.mkdir(parents=True)
    wheel.rename(retained)
    pinned_bytes = retained.read_bytes()
    package = {"kind": "pypi", "name": "sample", "version": "1.0", "sha256": checksum}
    check_packages(tmp_path, [package])
    with pytest.raises(ValueError, match="retain the pinned wheel"):
        check_packages(tmp_path, [{**package, "sha256": "f" * 64}])
    retained.write_bytes(b"corrupted archive")
    with pytest.raises(ValueError, match="retain the pinned wheel"):
        check_packages(tmp_path, [package])
    retained.write_bytes(pinned_bytes)
    with pytest.raises(ValueError, match="stale Python package"):
        check_packages(tmp_path, [{**package, "version": "2.0"}])
    module.write_bytes(b"old library code\n")
    with pytest.raises(ValueError, match="differs from pinned wheel"):
        check_packages(tmp_path, [package])


@pytest.mark.parametrize("layout", ["", "reference/"])
def test_923_missing_or_modified_bootstrap_inputs_cannot_certify(
    tmp_path: Path,
    layout: str,
) -> None:
    archive = tmp_path / "bootstrap.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        entry = tarfile.TarInfo(layout + "hg38ToHg19.over.chain.gz")
        entry.size = 4
        stream.addfile(entry, io.BytesIO(b"data"))
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Prepared reference input"):
        validate_bootstrap(tmp_path, archive.name, expected)
    chain = tmp_path / "hg38ToHg19.over.chain.gz"
    chain.write_bytes(b"data")
    validate_bootstrap(tmp_path, archive.name, expected)
    chain.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Prepared reference input"):
        validate_bootstrap(tmp_path, archive.name, expected)
    with pytest.raises(ValueError, match="not pinned"):
        validate_bootstrap(tmp_path, archive.name, "f" * 64)


@pytest.mark.parametrize("error", [KeyboardInterrupt, OSError, ValueError])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_923_interruption_or_postrun_failure_clears_pending_without_masking_original(
    error: type[BaseException],
    cleanup_fails: bool,
) -> None:
    states: list[str] = []

    def post(repo: str, sha: str, name: str, state: str, description: str) -> None:
        states.append(state)
        if cleanup_fails and state == "failure":
            raise OSError("GitHub unavailable during cleanup")

    original = error("original failure")

    def operation() -> tuple[str, str]:
        raise original

    with pytest.raises(error) as caught:
        publish_certification("owner/repo", "sha", "generic", operation, post)
    assert caught.value is original
    assert states == ["pending", "failure"]
