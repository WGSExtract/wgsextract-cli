"""Disposable machines for Install Lab's guest scenarios."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import apt_cache
import housekeeping
from windows_vm import docker, log


class Machine:
    """A disposable machine: created clean, destroyed after the run."""

    serve_host = "127.0.0.1"  # where the host's artifact server listens
    guest_host = "127.0.0.1"  # how the guest reaches it
    results_in_guest = ""
    guest_scripts_in_guest = ""

    def __init__(self, name: str, results: Path, guest: Path) -> None:
        self.name, self.results, self.guest = name, results, guest

    def __enter__(self) -> Machine:
        self.create()
        return self

    def __exit__(self, *exc: object) -> None:
        if os.environ.get("LAB_KEEP"):
            log(f"Keeping machine {self.name} (LAB_KEEP set)")
            return
        self.destroy()

    def create(self) -> None: ...
    def destroy(self) -> None: ...
    def exec(self, env: dict[str, str], command: str) -> int:
        raise NotImplementedError


class DockerMachine(Machine):
    guest_host = "host.docker.internal"
    results_in_guest = "/results"
    guest_scripts_in_guest = "/guest"
    use_apt_cache = True
    apt_profile = "linux"
    prerequisites: tuple[str, ...] = ()

    def __init__(self, name: str, results: Path, guest: Path) -> None:
        super().__init__(name, results, guest)
        self.docker = docker()

    def create(self) -> None:
        housekeeping.mark_used(f"docker:{apt_cache.UBUNTU_IMAGE}")
        if sys.platform.startswith("linux"):
            # Docker Desktop and OrbStack forward host.docker.internal to the
            # host's loopback; plain Linux Docker maps it to the bridge gateway.
            self.serve_host = subprocess.run(
                [self.docker, "network", "inspect", "bridge", "--format",
                 "{{(index .IPAM.Config 0).Gateway}}"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()  # fmt: skip
        try:
            with apt_cache.staged_cache(
                self.apt_profile, "amd64", enabled=self.use_apt_cache
            ) as cache_root:
                self.bootstrap(cache_root)
            # A normal user account; a desktop user can use sudo.
            user = "useradd -m -s /bin/bash tester && chown tester /results"
            if "sudo" in self.prerequisites:
                user += (
                    " && echo 'tester ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/tester"
                    " && chmod 440 /etc/sudoers.d/tester"
                )
            subprocess.run(
                [self.docker, "exec", self.name, "sh", "-c", user], check=True
            )
        except BaseException:
            self.destroy()
            raise

    def bootstrap(self, cache_root: Path) -> None:
        mounts, apt_command = apt_cache.container_apt(
            self.apt_profile,
            "amd64",
            (*apt_cache.LINUX_PACKAGES, *self.prerequisites),
            use_cache=self.use_apt_cache,
            keep_system_lists=True,
            cache_root=cache_root,
        )
        subprocess.run(
            [self.docker, "run", "-d", "--name", self.name, "--platform", "linux/amd64",
             "--add-host", "host.docker.internal:host-gateway", "-v", f"{self.results}:/results",
             "-v", f"{self.guest}:/guest:ro", *mounts, apt_cache.UBUNTU_IMAGE, "sleep", "infinity"],
            stdout=subprocess.DEVNULL, check=True,
        )  # fmt: skip
        apt = subprocess.run(
            [self.docker, "exec", self.name, "bash", "-c", apt_command],
            capture_output=True,
            text=True,
        )
        if apt.returncode:
            print(apt.stdout + apt.stderr, flush=True)
            raise SystemExit(f"Installing the prerequisites failed ({apt.returncode}).")
        for line in apt.stdout.splitlines():
            if line.startswith("::warning"):
                print(line, flush=True)

    def destroy(self) -> None:
        try:
            if sys.platform.startswith("linux"):
                # The container's users own what they wrote to /results; give
                # it back to the host user (Docker Desktop and OrbStack map
                # ownership), or the host can't write its verdict there.
                chown = subprocess.run(
                    [self.docker, "exec", self.name, "chown", "-R",
                     f"{os.getuid()}:{os.getgid()}", "/results"],
                    capture_output=True, text=True,
                )  # fmt: skip
                if chown.returncode:
                    log(f"could not take back /results: {chown.stderr.strip()}")
        finally:
            subprocess.run([self.docker, "rm", "-f", self.name], capture_output=True)

    def exec(self, env: dict[str, str], command: str) -> int:
        flags = [f"--env={k}={v}" for k, v in env.items()]
        return subprocess.run(
            [self.docker, "exec", "-u", "tester", "-w", "/home/tester", *flags,
             self.name, "bash", "-lc", command],
        ).returncode  # fmt: skip
