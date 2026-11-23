"""Share verified apt inputs between disposable Install Lab containers."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APT_INSTALL = REPO / ".github" / "actions" / "apt-install" / "apt-install.sh"
UBUNTU_IMAGE = "ubuntu:24.04"
CACHE_MOUNT = "/apt-cache"
CACHE_ROOT = REPO / "out" / "installlab-apt"
CACHE_SUBDIRS = ("lists", "archives")

# Install Lab's hosted cache keys hash this file and apt-install.sh.
LINUX_PACKAGES = ("curl", "ca-certificates")
LINUX_UPDATE_PACKAGES = ("git",)
LINUX_APP_PACKAGES = ("sudo", "python3", "fontconfig", "xvfb", "xauth")
WINDOWS_ISO_PACKAGES = ("aria2", "cabextract", "wimtools", "chntpw", "genisoimage")
WINDOWS_MEDIA_PACKAGES = ("genisoimage",)
WINDOWS_GUI_BUILD_PACKAGES = (
    "curl", "ca-certificates", "git", "build-essential", "make", "unzip", "nsis",
    "lld", "llvm", "clang", "clang-tools", "python3", "python3-defusedxml",
)  # fmt: skip
LINUX_GUI_BUILD_PACKAGES = (
    "curl", "ca-certificates", "git", "build-essential", "pkg-config", "file",
    "xdg-utils", "libwebkit2gtk-4.1-dev", "libssl-dev",
    "libayatana-appindicator3-dev", "librsvg2-dev",
)  # fmt: skip


def cache_path(root: Path, profile: str, arch: str) -> Path:
    return root / f"{UBUNTU_IMAGE.replace(':', '-')}-{arch}" / profile


@contextmanager
def staged_cache(
    profile: str, arch: str, *, enabled: bool, cache_root: Path = CACHE_ROOT
) -> Iterator[Path]:
    """Give a long-lived scenario a private copy and save it after bootstrap only."""
    if not enabled:
        yield cache_root
        return
    import fcntl

    cache = cache_path(cache_root, profile, arch)
    cache.mkdir(parents=True, exist_ok=True)
    with (
        (cache / ".install.lock").open("a") as lock,
        tempfile.TemporaryDirectory(prefix="stage-", dir=cache_root) as stage,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX)
        staged_root = Path(stage)
        staged = cache_path(staged_root, profile, arch)
        for name in CACHE_SUBDIRS:
            if (cache / name).is_dir():
                shutil.copytree(cache / name, staged / name, symlinks=True)
        yield staged_root
        # A running guest may still have handles to staged files after a rename.
        with tempfile.TemporaryDirectory(prefix="publish-", dir=cache_root) as publish:
            snapshot = Path(publish)
            for name in CACHE_SUBDIRS:
                shutil.copytree(staged / name, snapshot / name, symlinks=True)
            for name in CACHE_SUBDIRS:
                if (cache / name).is_dir():
                    shutil.rmtree(cache / name)
                (snapshot / name).rename(cache / name)


def container_apt(
    profile: str,
    arch: str,
    packages: tuple[str, ...],
    *,
    use_cache: bool,
    cache_root: Path,
    keep_system_lists: bool = False,
) -> tuple[list[str], str]:
    """Return Docker mounts and the shell command for the same apt install."""
    mounts = ["-v", f"{APT_INSTALL}:/apt-install.sh:ro"]
    command = ["bash", "/apt-install.sh"]
    if not use_cache:
        return mounts, shlex.join([*command, *packages])

    cache = cache_path(cache_root, profile, arch)
    cache.mkdir(parents=True, exist_ok=True)
    mounts.extend(("-v", f"{cache}:{CACHE_MOUNT}"))
    command.extend(("--cache-dir", CACHE_MOUNT))
    # apt may leave root-only partial dirs, which actions/cache cannot read.
    # Keep the regular lists available to the guest's own apt invocations.
    cleanup = f"rm -rf {CACHE_MOUNT}/{{lists,archives}}/{{partial,lock}}\n"
    if keep_system_lists:
        cleanup += (
            f'if [ "$status" -eq 0 ]; then '
            f"cp -a {CACHE_MOUNT}/lists/. /var/lib/apt/lists/; fi\n"
        )
    script = (
        f'status=0\n{shlex.join(command)} "$@" || status=$?\n'
        f"{cleanup}chown -R {os.getuid()}:{os.getgid()} {CACHE_MOUNT}\n"
        'exit "$status"'
    )
    return mounts, shlex.join(["bash", "-ec", script, "--", *packages])


def run_with_apt(
    docker: str,
    arch: str,
    docker_args: list[str],
    command: list[str],
    profile: str,
    packages: tuple[str, ...],
    *,
    use_cache: bool,
) -> None:
    """Bootstrap a container, then run its build with no shared cache mounted."""
    guest = ""
    try:
        with staged_cache(profile, arch, enabled=use_cache) as cache_root:
            mounts, apt = container_apt(
                profile, arch, packages, use_cache=use_cache, cache_root=cache_root
            )
            guest = subprocess.check_output(
                [docker, "run", "--rm", "-d", "--platform", f"linux/{arch}", *mounts,
                 *docker_args, UBUNTU_IMAGE, "sleep", "infinity"],
                text=True,
            ).strip()  # fmt: skip
            subprocess.run([docker, "exec", guest, "bash", "-ec", apt], check=True)
        subprocess.run([docker, "exec", guest, *command], check=True)
    finally:
        if guest:
            subprocess.run([docker, "rm", "-f", guest], capture_output=True)
