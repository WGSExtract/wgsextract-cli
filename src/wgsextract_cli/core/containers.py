"""Running tools from Docker images: Parabricks on a GPU, Google's DeepVariant.

The CLI doesn't install Docker or pull images; it checks for them and says
what's missing. A tool runs in a container that sees the files it reads and
writes at their own paths, as the calling user, so paths in arguments need no
translation and outputs aren't owned by root.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.reference_lookup import REF_PATH
from wgsextract_cli.core.steps import RUN_TAG
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    WGSExtractError,
    popen,
    run_command,
)

# Seconds a failed container's OOM event may trail its exit.
OOM_EVENT_GRACE = 5.0
# Labels each container with the step run (RUN_TAG) that started it.
STEP_LABEL = "wgsextract.step"
# A container's REF_PATH: set and local, so htslib never asks a server.
CONTAINER_REF_PATH = "/tmp/hts-ref/%2s/%2s/%s"


def docker(setup: str) -> str:
    path = get_tool_path("docker")
    if not path:
        raise MissingDependencyError(f"Docker not found. {setup}")
    return path


def docker_info(setup: str, field: str = "{{json .ServerVersion}}") -> str:
    """`docker info --format <field>`, raising when the daemon isn't usable."""
    info = run_command(
        [docker(setup), "info", "--format", field], capture_output=True, check=False
    )
    if info.returncode != 0:
        raise MissingDependencyError(
            f"Docker isn't usable: {(info.stderr or '').strip()}. Start the Docker "
            "service, and add your user to the docker group (then log in again)."
        )
    return info.stdout or ""


def require_image(image: str, missing: str) -> None:
    inspect = [docker(missing), "image", "inspect", image]
    if run_command(inspect, capture_output=True, check=False).returncode != 0:
        raise MissingDependencyError(missing)


def image_record(image: str) -> dict[str, str]:
    """What a step record says ran: ``image``, a reference Docker can pull,
    and for a tag ``image_id``, the local ID of the build the tag named, so a
    replaced tag reruns (#601).

    ``image`` is the tag's registry digest (``repo@sha256:...``) when Docker
    has one, else the tag itself, as for a locally built image. A reference
    already pinned to a digest (#597), or one Docker can't inspect, is kept.
    """
    if "@" in image:
        return {"image": image}
    docker_path = get_tool_path("docker")
    if not docker_path:
        return {"image": image}
    inspected = run_command(
        [docker_path, "image", "inspect", image], capture_output=True, check=False
    )
    try:
        found = (
            json.loads(inspected.stdout or "")[0] if inspected.returncode == 0 else {}
        )
    except (ValueError, IndexError, TypeError):
        found = {}
    image_id = found.get("Id") if isinstance(found, dict) else None
    if not image_id:
        return {"image": image}
    return {
        "image": _pull_reference(image, found.get("RepoDigests") or []),
        "image_id": image_id,
    }


def _pull_reference(image: str, repo_digests: list[str]) -> str:
    """``image``'s repo pinned to the digest Docker pulled it by, else ``image``."""
    name, _, tag = image.rpartition(":")
    repo = name if name and "/" not in tag else image
    short = repo.removeprefix("docker.io/").removeprefix("library/")
    for digest in repo_digests:
        digest_repo = digest.partition("@")[0]
        if digest_repo.removeprefix("docker.io/").removeprefix("library/") == short:
            return digest
    return image


def host_path(path: str) -> str:
    """The absolute path a file has both outside and inside the container."""
    return os.path.abspath(path)


def link_chain(path: str) -> list[str]:
    """``path`` and, if it is a symlink, each file it leads to: given to
    run_container, every folder on the way to its target is mounted, so the
    link still opens in the container (pbrun takes an interval file's format
    from its name, so ``panel.bed`` linked to a store's ``dataset_7.dat`` is
    passed as itself)."""
    chain = [path]
    while Path(chain[-1]).is_symlink() and len(chain) <= 40:
        link = chain[-1]
        chain.append(host_path(os.path.join(os.path.dirname(link), os.readlink(link))))
    target = str(Path(path).resolve())
    return chain if target in chain else [*chain, target]


def run_container(
    image: str,
    command: list[str],
    paths: Iterable[str | None],
    *,
    gpus: bool = False,
    entrypoint: str | None = None,
    temporary_directory: str | None = None,
    local_only: bool = False,
    oom_error: str | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` in ``image``. ``paths`` are the files and directories it
    reads or writes, and ``command`` names them by host_path; their directories
    are mounted at the same paths in the container.

    With ``oom_error``, an out-of-memory kill of any process in the container
    stops it and raises that message, for tools such as DeepVariant whose
    worker pools wait forever on a killed worker. ``capture_output`` returns
    its output instead of showing it."""
    mounts = sorted(
        {host_path(p) if Path(p).is_dir() else str(Path(host_path(p)).parent)
         for p in paths if p}
    )  # fmt: skip
    cmd = [docker(""), "run", "--rm", *(["--gpus", "all"] if gpus else [])]
    if local_only:
        cmd += ["--pull=never"]
    if entrypoint is not None:
        cmd += ["--entrypoint", entrypoint]
    # The user has no home in the image; tools that write settings use /tmp.
    # Docker Desktop on Windows maps file ownership itself and has no POSIX ids.
    if sys.platform != "win32":
        cmd += ["-u", f"{os.getuid()}:{os.getgid()}"]
    home = host_path(temporary_directory) if temporary_directory else "/tmp"
    cmd += ["-e", f"HOME={home}"]
    if temporary_directory:
        cmd += ["-e", f"TMPDIR={home}"]
    # The image's htslib may be old enough to fetch a reference it can't find
    # from EBI unless REF_PATH is set; the host's caches aren't mounted, so it
    # gets an empty folder of its own (#636).
    cmd += ["-e", f"{REF_PATH}={CONTAINER_REF_PATH}"]
    for mount in mounts:
        cmd += ["-v", f"{mount}:{mount}"]
    name = f"wgsextract-{uuid.uuid4().hex[:12]}"
    cmd += ["--name", name]
    if tag := os.environ.get(RUN_TAG):
        # A rerun finds the container of a step whose wgsextract was killed.
        cmd += ["--label", f"{STEP_LABEL}={tag}"]
    cmd += ["-w", mounts[0], image, *command]
    try:
        if oom_error is None:
            return run_command(cmd, capture_output=capture_output)
        with _oom_watch(cmd[0], name) as oom_killed:
            try:
                return run_command(cmd, capture_output=capture_output)
            except subprocess.CalledProcessError as error:
                if oom_killed.wait(OOM_EVENT_GRACE):
                    raise WGSExtractError(oom_error) from error
                raise
    except (KeyboardInterrupt, SystemExit):
        # Stopping `docker run` leaves its container running, and a shell as
        # the container's PID 1 ignores the signals it forwards. engine_call works
        # after the cancelled process registry refuses new ones.
        with suppress(OSError, subprocess.SubprocessError):
            engine_call([cmd[0], "kill", name])
        raise


def engine_call(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """A quick container engine query or command, bounded in case the daemon
    hangs. Unlike run_command, it works once the run has been cancelled."""
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", timeout=30, check=False
    )


def tagged_containers(tags: Iterable[str]) -> list[str]:
    """Running containers that step runs tagged with one of ``tags`` started.
    Empty when there is no container engine, or it doesn't answer."""
    docker_path = get_tool_path("docker")
    if not docker_path:
        return []
    found: list[str] = []
    for tag in sorted(tags):
        try:
            listed = engine_call(
                [docker_path, "ps", "--filter", f"label={STEP_LABEL}={tag}",
                 "--format", "{{.Names}}"]
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError) as error:
            logging.debug(f"Couldn't list containers: {error}")
            return found
        if listed.returncode != 0:
            logging.debug(f"Couldn't list containers: {listed.stderr.strip()}")
            return found
        found += [f"container {name}" for name in listed.stdout.split()]
    return found


@contextmanager
def _oom_watch(docker_path: str, name: str) -> Iterator[threading.Event]:
    """Set the yielded event, and kill container ``name``, on its first OOM kill.

    Docker reports an "oom" event when the container's cgroup records an OOM
    kill, whether from a --memory cap or the host running out of memory.
    """
    killed = threading.Event()
    try:
        events = popen(
            [docker_path, "events", "--filter", f"container={name}",
             "--filter", "event=oom", "--format", "{{.Action}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip
    except OSError as error:
        logging.warning(f"Out-of-memory kills won't be detected: {error}")
        yield killed
        return

    def watch() -> None:
        assert events.stdout is not None
        for _line in events.stdout:
            if killed.is_set():
                continue
            logging.error(f"A process in container {name} ran out of memory.")
            run_command([docker_path, "kill", name], capture_output=True, check=False)
            # Only now: the caller reports the OOM once this is set.
            killed.set()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        yield killed
    finally:
        events.terminate()
        try:
            events.wait(timeout=5)
        except subprocess.TimeoutExpired:
            events.kill()
