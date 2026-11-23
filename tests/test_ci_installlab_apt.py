"""Install Lab's container apt cache policy and opt-in real Docker smoke."""

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.installlab.apt_cache import (
    UBUNTU_IMAGE,
    cache_path,
    container_apt,
    staged_cache,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "installlab"
WORKFLOW = ROOT / ".github" / "workflows" / "install_lab.yml"
# pytest isolates HOME; keep Docker's selected context for this opt-in test.
DOCKER_CONFIG = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker"))


def test_1465_install_lab_cannot_bypass_the_container_apt_cache() -> None:
    callers = set()
    for path in SCRIPTS.glob("*.py"):
        if path.name == "apt_cache.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "apt-install.sh" not in text, path
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.get_source_segment(text, node) or ""
            if any(
                f"apt_cache.{helper}(" in body
                for helper in ("container_apt", "run_with_apt")
            ):
                callers.add((path.name, node.name))
    assert callers == {
        ("docker_machine.py", "bootstrap"),
        ("windows_vm.py", "build_iso"),
        ("windows_vm.py", "make_iso"),
        ("gui_build.py", "in_container"),
    }


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX Docker host")
@pytest.mark.parametrize("use_cache", [True, False])
def test_1465_container_apt_mount_matches_cache_flag(
    tmp_path: Path, use_cache: bool
) -> None:
    mounts, command = container_apt(
        "test", "amd64", ("jq",), use_cache=use_cache, cache_root=tmp_path
    )
    if use_cache:
        cache_path = next(item for item in mounts if item.startswith(str(tmp_path)))
        host, guest = cache_path.split(":")
        assert Path(host).is_dir()
        assert f"--cache-dir {guest}" in command
    else:
        assert "--cache-dir" not in command
        assert not list(tmp_path.iterdir())
        assert not any(item.startswith(str(tmp_path)) for item in mounts)


@pytest.mark.parametrize("job", ["linux", "linux-app"])
def test_1465_hosted_install_lab_restores_and_saves_the_container_cache(
    job: str,
) -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    body = re.split(r"\n  [\w-]+:\n", text.split(f"\n  {job}:\n", 1)[1], maxsplit=1)[0]
    restore, install, save = (
        next(step for step in body.split("      - ") if match in step)
        for match in (
            "uses: actions/cache/restore@",
            "lab.py",
            "uses: actions/cache/save@",
        )
    )
    assert body.index(restore) < body.index(install) < body.index(save)
    path = f"path: out/installlab-apt/ubuntu-24.04-amd64/{job}\n"
    assert path in restore and path in save
    assert "restore-keys:" in restore
    assert "scripts/installlab/apt_cache.py" in restore
    assert ".github/actions/apt-install/apt-install.sh" in restore
    assert "refs/heads/main" in save
    assert "!inputs.no_apt_cache" in restore and "!inputs.no_apt_cache" in save
    assert "--no-apt-cache" in install


@pytest.mark.remote
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX Docker host")
def test_1465_warm_apt_cache_installs_without_ubuntu_mirrors(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker is not installed")
    client = [docker, "--config", str(DOCKER_CONFIG)]
    daemon = subprocess.run(
        [*client, "info", "--format", "{{.OSType}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if daemon.returncode or daemon.stdout.strip() != "linux":
        pytest.skip("Docker's Linux engine is not available")

    run = [*client, "run", "--rm", "--platform", "linux/amd64"]
    with staged_cache("test", "amd64", enabled=True, cache_root=tmp_path) as stage:
        mounts, command = container_apt(
            "test", "amd64", ("jq",), use_cache=True, cache_root=stage
        )
        subprocess.run(
            [*run, *mounts, UBUNTU_IMAGE, "bash", "-ec", command],
            check=True,
            timeout=240,
        )

    guest = ""
    try:
        with staged_cache("test", "amd64", enabled=True, cache_root=tmp_path) as stage:
            mounts, command = container_apt(
                "test",
                "amd64",
                ("jq",),
                use_cache=True,
                keep_system_lists=True,
                cache_root=stage,
            )
            guest = subprocess.check_output(
                [*run, *mounts, "-d", "--network", "none", "--pull", "never",
                 UBUNTU_IMAGE, "sleep", "infinity"],
                text=True, timeout=30,
            ).strip()  # fmt: skip
            offline = subprocess.run(
                [*client, "exec", guest, "bash", "-ec",
                 f"{command}\njq --version\napt-cache show git > /dev/null\n"
                 "test -s /etc/apt/ci-ubuntu-mirrors.txt"],
                capture_output=True, text=True, timeout=90,
            )  # fmt: skip
            assert offline.returncode == 0, offline.stdout + offline.stderr
            assert "from the cached package lists and .debs" in offline.stdout
            assert "jq-" in offline.stdout
        subprocess.run(
            [*client, "exec", guest, "sh", "-c", "rm -f /apt-cache/archives/*.deb"],
            check=True,
            timeout=30,
        )
        cache = cache_path(tmp_path, "test", "amd64")
        assert list((cache / "archives").glob("jq_*.deb"))
    finally:
        if guest:
            subprocess.run([*client, "rm", "-f", guest], check=True, timeout=30)
