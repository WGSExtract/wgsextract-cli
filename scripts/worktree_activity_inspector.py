#!/usr/bin/env python3
"""Answer bounded worktree activity queries on a systemd-activated Unix socket."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import signal
import socket
import stat
import struct
from collections import deque
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import NamedTuple, TextIO

import tomllib

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
REG = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PROC = Path("/proc")
LIMIT = 16_384
MAX_NAMES = 64
TERMINAL = {"Z", "X"}


class Unverifiable(Exception):
    """The request or the observed filesystem/process state was not safe."""

    def __init__(self, pid: int | None = None) -> None:
        self.pid = pid


@contextmanager
def directory(parent: int, name: str) -> Iterator[int]:
    fd = os.open(name, DIR, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def record(parent: int, name: str, owner: int) -> str:
    fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1:
            raise Unverifiable
        with open(f"/proc/self/fd/{fd}", "rb") as data:
            raw = data.read(4097)
    finally:
        os.close(fd)
    if len(raw) > 4096:
        raise Unverifiable
    return os.fsdecode(raw).removesuffix("\n")


def identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


@contextmanager
def registered(
    root: Path, uid: int, names: list[str]
) -> Iterator[tuple[tuple[int, int], dict[str, tuple[int, int]]]]:
    with ExitStack() as stack:
        base = stack.enter_context(directory(-1, "/"))
        for part in root.parts[1:]:
            base = stack.enter_context(directory(base, part))
        scratch = stack.enter_context(directory(base, "scratch"))
        git = stack.enter_context(directory(base, ".git"))
        worktrees = stack.enter_context(directory(git, "worktrees"))
        if any(os.fstat(fd).st_uid != uid for fd in (base, scratch, git, worktrees)):
            raise Unverifiable
        result = {}
        for name in names:
            with directory(scratch, name) as candidate:
                if os.fstat(candidate).st_uid != uid:
                    raise Unverifiable
                marker = record(candidate, ".git", uid)
                prefix = f"gitdir: {root}/.git/worktrees/"
                entry = marker.removeprefix(prefix)
                if entry == marker or not NAME.fullmatch(entry):
                    raise Unverifiable
                with directory(worktrees, entry) as admin:
                    if os.fstat(admin).st_uid != uid:
                        raise Unverifiable
                    if (
                        record(admin, "gitdir", uid)
                        != str(root / "scratch" / name / ".git")
                        or record(admin, "commondir", uid) != "../.."
                    ):
                        raise Unverifiable
                result[name] = identity(candidate)
        yield identity(base), result


def proc_text(fd: int, name: str) -> TextIO:
    return os.fdopen(os.open(name, REG, dir_fd=fd), "r", errors="surrogateescape")


def process_state(fd: int) -> tuple[str, tuple[int, ...], bool]:
    with proc_text(fd, "status") as data:
        fields = dict(line.split(":", 1) for line in data if ":" in line)
    uids = tuple(map(int, fields["Uid"].split()))
    state = fields["State"].split()[0]
    kernel = fields.get("Kthread", "").strip()
    if len(uids) != 4 or len(state) != 1 or kernel not in {"", "0", "1"}:
        raise Unverifiable
    return state, uids, kernel == "1"


def start_time(fd: int) -> str:
    # The comm field may contain spaces and parentheses.
    with proc_text(fd, "stat") as data:
        return data.read().rsplit(")", 1)[1].split()[19]


def require_group_exited(fd: int, leader: str) -> None:
    # pthread_exit can leave a zombie leader while worker threads still run.
    # The leader's cwd/FDs are not an activity view of those workers.
    try:
        with directory(fd, "task") as tasks:
            if os.listdir(tasks) != [leader] or process_state(fd)[0] not in TERMINAL:
                raise Unverifiable
    except (FileNotFoundError, ProcessLookupError):
        try:
            process_state(fd)
        except (FileNotFoundError, ProcessLookupError):
            return
        raise Unverifiable from None


class Mount(NamedTuple):
    device: str
    backing: Path
    point: Path
    number: int = 0
    parent: int = 0


def mount_path(value: str) -> Path:
    return Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value))


def mounts(fd: int) -> set[Mount]:
    with proc_text(fd, "mountinfo") as table:
        return {
            Mount(
                fields[2],
                mount_path(fields[3]),
                mount_path(fields[4]),
                int(fields[0]),
                int(fields[1]),
            )
            for line in table
            if (fields := line.split(maxsplit=5))
        }


def check_mounts(records: set[Mount], candidates: dict[Path, tuple[int, int]]) -> None:
    for path in candidates:
        covering = [row for row in records if path.is_relative_to(row.point)]
        if not covering:
            raise Unverifiable
        depth = max(len(row.point.parts) for row in covering)
        deepest = [row for row in covering if len(row.point.parts) == depth]
        if len(deepest) != 1:
            raise Unverifiable
        anchor = deepest[0]
        backing = anchor.backing / path.relative_to(anchor.point)
        for row in records:
            if row.point != path and row.point.is_relative_to(path):
                raise Unverifiable
            if row.device != anchor.device:
                continue
            if backing.is_relative_to(row.backing):
                if row.point / backing.relative_to(row.backing) != path:
                    raise Unverifiable
            elif row.backing.is_relative_to(backing):
                if row.point != path / row.backing.relative_to(backing):
                    raise Unverifiable


@contextmanager
def process_root(fd: int) -> Iterator[tuple[int, Path]]:
    label = Path(os.readlink("root", dir_fd=fd))
    if not label.is_absolute():
        raise Unverifiable
    root = os.open("root", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=fd)
    try:
        yield root, label
    finally:
        os.close(root)


def mount_number(fd: int) -> int:
    with (PROC / "self" / "fdinfo" / str(fd)).open() as data:
        for line in data:
            if line.startswith("mnt_id:"):
                return int(line.partition(":")[2])
    raise Unverifiable


def visible_mount(root: int, path: Path) -> tuple[int, tuple[int, int] | None]:
    """Identify the mount at the path, or at its last accessible ancestor."""
    with ExitStack() as stack:
        current = root
        for part in path.parts[1:]:
            try:
                fd = os.open(
                    part, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current
                )
            except (FileNotFoundError, NotADirectoryError):
                return mount_number(current), None
            stack.callback(os.close, fd)
            if stat.S_ISLNK(os.fstat(fd).st_mode):
                raise Unverifiable
            current = fd
        return mount_number(current), identity(current)


def target_mount(records: set[Mount], path: Path) -> Mount:
    covering = [record for record in records if path.is_relative_to(record.point)]
    if not covering:
        raise Unverifiable
    deepest = max(len(row.point.parts) for row in covering)
    choices = [row for row in covering if len(row.point.parts) == deepest]
    if len(choices) != 1:
        raise Unverifiable
    return choices[0]


def check_view(
    fd: int,
    root: int,
    base: Path,
    host_mounts: set[Mount],
    candidates: dict[Path, tuple[int, int]],
) -> tuple[set[Mount], set[int], set[Path]]:
    records = mounts(fd)
    devices: set[int] = set()
    covered_aliases: set[Path] = set()
    if base != Path("/"):
        with directory(-1, "/") as observer:
            root_identity = identity(root)
            if visible_mount(observer, base)[1] != root_identity and any(
                expected[0] == root_identity[0] for expected in candidates.values()
            ):
                raise Unverifiable
    for path, expected in candidates.items():
        if base == Path("/") and records == host_mounts:
            with ExitStack() as stack:
                current = root
                for part in path.parts[1:]:
                    current = stack.enter_context(directory(current, part))
                if identity(current) != expected:
                    raise Unverifiable
            continue
        host = target_mount(host_mounts, path)
        backing = host.backing / path.relative_to(host.point)
        visible: Mount | None = None
        visible_identity = None
        if path.is_relative_to(base):
            own_path = Path("/") / path.relative_to(base)
            canonical_id, visible_identity = visible_mount(root, own_path)
            visible = next((row for row in records if row.number == canonical_id), None)
            if visible is None:
                raise Unverifiable
        for row in records:
            if row.device != host.device:
                continue
            if backing.is_relative_to(row.backing):
                projected = row.point / backing.relative_to(row.backing)
                canonical = path
            elif row.backing.is_relative_to(backing):
                projected = row.point
                canonical = path / row.backing.relative_to(backing)
            else:
                continue
            if base / projected.relative_to("/") != canonical:
                if visible_mount(root, projected)[0] == row.number:
                    raise Unverifiable
                # Its mount may still be used through a retained handle or
                # an interpreter's path after the source descriptor closed.
                devices.add(expected[0])
                covered_aliases.add(projected)
        if visible_identity != expected:
            if (
                visible is not None
                and visible.device == host.device
                and visible.backing / own_path.relative_to(visible.point) == backing
            ):
                raise Unverifiable
            devices.add(expected[0])
    return records, devices, covered_aliases


def resolve_argument(
    root: int, argument: Path, mounts: set[Mount], covered_aliases: set[Path]
) -> Path | None:
    """Follow argv path metadata within the target process's pinned root."""
    remaining = deque(argument.parts[1:])
    links = 0
    with ExitStack() as stack:
        resolved: list[tuple[str, int]] = [("", root)]
        while remaining:
            if covered_aliases:
                lookup = Path("/", *(name for name, _ in resolved[1:]), *remaining)
                if any(lookup.is_relative_to(alias) for alias in covered_aliases):
                    raise Unverifiable
            part = remaining.popleft()
            if part == "..":
                if len(resolved) > 1:
                    resolved.pop()
                continue
            parent = resolved[-1][1]
            try:
                fd = os.open(
                    part, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
                )
                stack.callback(os.close, fd)
                mode = os.fstat(fd).st_mode
            except PermissionError:
                path = Path("/", *(name for name, _ in resolved[1:]), part)
                # Access-controlled filesystems can refuse even O_PATH on
                # their root. That exact mountpoint cannot be a symlink;
                # any inaccessible name below it remains unverifiable.
                if not remaining and any(
                    row.point == path and row.parent == mount_number(parent)
                    for row in mounts
                ):
                    return path
                raise
            if stat.S_ISLNK(mode):
                links += 1
                if links > 40:
                    return None
                target = Path(os.readlink("", dir_fd=fd))
                if target.is_absolute():
                    resolved = resolved[:1]
                remaining.extendleft(
                    reversed(target.parts[1:] if target.is_absolute() else target.parts)
                )
            else:
                if remaining and not stat.S_ISDIR(mode):
                    return None
                resolved.append((part, fd))
    return Path("/", *(name for name, _ in resolved[1:]))


def observed_path(
    owner: int,
    name: str,
    root: int,
    base: Path,
    devices: set[int],
    candidates: dict[Path, tuple[int, int]],
) -> Path | None:
    text = os.readlink(name, dir_fd=owner)
    if not text.startswith("/"):
        return None
    path = Path(text.removesuffix(" (deleted)"))
    if devices and not any(path.is_relative_to(candidate) for candidate in candidates):
        ref = os.open(name, os.O_PATH | os.O_CLOEXEC, dir_fd=owner)
        try:
            observed = identity(ref)
            if observed[0] in devices:
                if path.is_relative_to(base):
                    relative = Path("/") / path.relative_to(base)
                    if visible_mount(root, relative) != (mount_number(ref), observed):
                        raise Unverifiable
                else:
                    with directory(-1, "/") as observer:
                        if visible_mount(observer, path)[1] != observed:
                            raise Unverifiable
        finally:
            os.close(ref)
    return path


def fs_targets(
    fd: int,
    root: int,
    base: Path,
    devices: set[int],
    candidates: dict[Path, tuple[int, int]],
) -> Iterator[Path]:
    cwd = observed_path(fd, "cwd", root, base, devices, candidates)
    if cwd is None:
        raise Unverifiable
    yield cwd
    with directory(fd, "fd") as descriptors:
        for number in os.listdir(descriptors):
            try:
                path = observed_path(
                    descriptors, number, root, base, devices, candidates
                )
            except FileNotFoundError:
                # The descriptor closed between listing and reading it.
                continue
            if path is not None:
                yield path


def process_targets(
    fd: int,
    root: int,
    base: Path,
    pid: str,
    host_mounts: set[Mount],
    candidates: dict[Path, tuple[int, int]],
    devices: set[int],
    records: set[Mount],
    covered_aliases: set[Path],
) -> Iterator[Path]:
    yield base
    observed = fs_targets(fd, root, base, devices, candidates)
    cwd = next(observed)
    if not cwd.is_absolute():
        raise Unverifiable
    yield cwd
    yield from observed
    executable = observed_path(fd, "exe", root, base, devices, candidates)
    if executable is None:
        raise Unverifiable
    yield executable
    # A Linux thread can unshare its filesystem context or descriptor table.
    namespace = os.readlink("ns/mnt", dir_fd=fd)
    root_id = identity(root)
    with directory(fd, "task") as tasks:
        for tid in os.listdir(tasks):
            if tid == pid:
                continue
            try:
                with directory(tasks, tid) as thread:
                    with process_root(thread) as (thread_root, thread_base):
                        yield thread_base
                        thread_devices = devices
                        if (
                            os.readlink("ns/mnt", dir_fd=thread) != namespace
                            or identity(thread_root) != root_id
                            or thread_base != base
                        ):
                            _, thread_devices, _ = check_view(
                                thread,
                                thread_root,
                                thread_base,
                                host_mounts,
                                candidates,
                            )
                        yield from fs_targets(
                            thread, thread_root, thread_base, thread_devices, candidates
                        )
            except (FileNotFoundError, ProcessLookupError):
                try:
                    with directory(tasks, tid) as thread:
                        if process_state(thread)[0] not in TERMINAL:
                            raise Unverifiable from None
                except (FileNotFoundError, ProcessLookupError):
                    continue
    with proc_text(fd, "maps") as data:
        for line in data:
            parts = line.rstrip("\n").split(maxsplit=5)
            if len(parts) == 6 and parts[5].startswith("/"):
                path = Path(parts[5].replace(r"\012", "\n").removesuffix(" (deleted)"))
                if devices and not any(
                    path.is_relative_to(candidate) for candidate in candidates
                ):
                    major, minor = parts[3].split(":")
                    mapped = (os.makedev(int(major, 16), int(minor, 16)), int(parts[4]))
                    if mapped[0] in devices:
                        if path.is_relative_to(base):
                            relative = Path("/") / path.relative_to(base)
                            existing = visible_mount(root, relative)[1]
                        else:
                            with directory(-1, "/") as observer:
                                existing = visible_mount(observer, path)[1]
                        if existing != mapped:
                            raise Unverifiable
                yield path
    with os.fdopen(os.open("cmdline", REG, dir_fd=fd), "rb") as data:
        arguments = data.read(2**21 + 1)
    if len(arguments) > 2**21 or not arguments.endswith(b"\0"):
        raise Unverifiable
    # A chroot on a different device with no mounted children has no
    # pathname route to a candidate, including via procfs magic links.
    separate_root = (
        base != Path("/")
        and not records
        and all(identity(root)[0] != expected[0] for expected in candidates.values())
    )
    for raw in arguments.split(b"\0"):
        if raw.startswith(b"-"):
            _, _, raw = raw.partition(b"=")
        if not raw:
            continue
        arg = Path(os.fsdecode(raw))
        if base != Path("/"):
            # An interpreter can keep argv after entering a private root.
            yield Path(os.path.normpath(arg if arg.is_absolute() else cwd / arg))
        if not arg.is_absolute():
            if not cwd.is_relative_to(base):
                raise Unverifiable
            arg = Path("/") / cwd.relative_to(base) / arg
        else:
            arg = Path("/") / str(arg).lstrip("/")
        lexical = Path(os.path.normpath(arg))
        if any(lexical.is_relative_to(alias) for alias in covered_aliases):
            raise Unverifiable
        yield base / lexical.relative_to("/")
        if len(raw) <= 4096 and not separate_root:
            # An argument need not name a file. Never open its contents.
            try:
                resolved = resolve_argument(root, arg, records, covered_aliases)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as error:
                if error.errno != errno.ENAMETOOLONG:
                    raise
                continue
            if resolved is not None:
                yield base / resolved.relative_to("/")


def host_mount_table(proc: int) -> set[Mount]:
    with directory(proc, "1") as initial:
        return mounts(initial)


def process_ids(fd: int) -> Iterator[str]:
    return (name for name in os.listdir(fd) if name.isascii() and name.isdecimal())


def require_complete_proc() -> None:
    with (PROC / "self/mountinfo").open(errors="surrogateescape") as table:
        for line in table:
            before, after = line.split(" - ", 1)
            mount, filesystem = before.split(), after.split()
            if mount[4] == str(PROC) and filesystem[0] == "proc":
                options = (mount[5] + "," + filesystem[2]).split(",")
                if any(
                    option.startswith(("hidepid=", "subset="))
                    and option not in {"hidepid=0", "hidepid=off"}
                    for option in options
                ):
                    raise Unverifiable


def inspect(
    root: Path, uid: int, repo_id: tuple[int, int], wanted: dict[str, tuple[int, int]]
) -> dict[str, list[int]]:
    require_complete_proc()
    names = list(wanted)
    result: dict[str, list[int]] = {name: [] for name in names}
    candidates = {root / "scratch" / name: wanted[name] for name in names}
    with registered(root, uid, names) as before, directory(-1, str(PROC)) as proc:
        if before != (repo_id, wanted):
            raise Unverifiable
        host_mounts = host_mount_table(proc)
        check_mounts(host_mounts, candidates)
        for name in process_ids(proc):
            try:
                with directory(proc, name) as fd:
                    state, uids, kernel = process_state(fd)
                    if kernel:
                        continue
                    if state in TERMINAL:
                        require_group_exited(fd, name)
                        continue
                    start = start_time(fd)
                    namespace = os.readlink("ns/mnt", dir_fd=fd)
                    seen: set[str] = set()
                    with process_root(fd) as (view, base):
                        records, devices, covered_aliases = check_view(
                            fd, view, base, host_mounts, candidates
                        )
                        for path in process_targets(
                            fd,
                            view,
                            base,
                            name,
                            host_mounts,
                            candidates,
                            devices,
                            records,
                            covered_aliases,
                        ):
                            seen.update(
                                p.name for p in candidates if path.is_relative_to(p)
                            )
                        state, live_uids, live_kernel = process_state(fd)
                        if state in TERMINAL:
                            require_group_exited(fd, name)
                            continue
                        with process_root(fd) as (latest_view, latest_base):
                            if latest_base != base or identity(latest_view) != identity(
                                view
                            ):
                                raise Unverifiable
                        if (
                            start_time(fd) != start
                            or live_uids != uids
                            or live_kernel
                            or os.readlink("ns/mnt", dir_fd=fd) != namespace
                            or mounts(fd) != records
                        ):
                            raise Unverifiable
                    for found in seen:
                        result[found].append(int(name))
            except (FileNotFoundError, ProcessLookupError):
                # A live PID missing inspection state is not an exiting process.
                try:
                    with directory(proc, name) as current:
                        require_group_exited(current, name)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except (OSError, ValueError, KeyError, IndexError, Unverifiable):
                    raise Unverifiable(int(name)) from None
            except (OSError, ValueError, KeyError, IndexError, Unverifiable):
                raise Unverifiable(int(name)) from None
        with registered(root, uid, names) as after:
            if after != before:
                raise Unverifiable
    return result


def parse_identity(value: object) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(part) is not int or not 0 <= part < 2**64 for part in value)
    ):
        raise Unverifiable
    return int(value[0]), int(value[1])


def reply(request: bytes, root: Path, uid: int, digest: str) -> dict[str, object]:
    if not request.endswith(b"\n") or len(request) > LIMIT:
        raise Unverifiable
    query = json.loads(request)
    if (
        not isinstance(query, dict)
        or query.keys() != {"repository", "candidates"}
        or not isinstance(candidates := query["candidates"], dict)
        or not 0 < len(candidates) <= MAX_NAMES
        or any(not NAME.fullmatch(name) for name in candidates)
    ):
        raise Unverifiable
    repo_id = parse_identity(query["repository"])
    wanted = {name: parse_identity(value) for name, value in candidates.items()}
    return {"sha256": digest, "busy": inspect(root, uid, repo_id, wanted)}


def handle(connection: socket.socket, root: Path, uid: int, digest: str) -> None:
    connection.settimeout(5)
    _, peer_uid, _ = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    if peer_uid not in {0, uid}:
        return
    try:
        with connection.makefile("rb") as request:
            response = reply(request.readline(LIMIT + 1), root, uid, digest)
        payload = json.dumps(response, separators=(",", ":")).encode()
        if len(payload) + 1 > LIMIT:
            raise Unverifiable
    except Unverifiable as error:
        failure: dict[str, str | int] = {"error": "unverifiable"}
        if error.pid is not None:
            failure["pid"] = error.pid
        payload = json.dumps(failure, separators=(",", ":")).encode()
    except (OSError, ValueError, KeyError, IndexError, RuntimeError):
        payload = b'{"error":"unverifiable"}'
    connection.sendall(payload + b"\n")


def serve(listener: socket.socket, root: Path, uid: int, digest: str) -> None:
    def timed_out(_signal: int, _frame: object) -> None:
        raise Unverifiable

    signal.signal(signal.SIGALRM, timed_out)
    while True:
        connection, _ = listener.accept()
        with connection:
            try:
                signal.alarm(35)
                try:
                    handle(connection, root, uid, digest)
                finally:
                    signal.alarm(0)
            except (OSError, Unverifiable):
                continue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.geteuid() != 0
        or os.environ.get("LISTEN_PID") != str(os.getpid())
        or os.environ.get("LISTEN_FDS") != "1"
    ):
        parser.error("requires root and one systemd socket")
    with os.fdopen(os.open(args.config, REG), "rb") as config:
        info = os.fstat(config.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            parser.error("expected a root-owned configuration file")
        settings = tomllib.load(config)
    root, uid = Path(settings["repository"]), settings["uid"]
    if (
        not root.is_absolute()
        or root == Path("/")
        or root.resolve(strict=True) != root
        or type(uid) is not int
        or not 0 < uid < 2**31
    ):
        parser.error("expected a canonical primary repository and non-root uid")
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with socket.socket(fileno=3) as listener:
        if listener.family != socket.AF_UNIX or not listener.getsockopt(
            socket.SOL_SOCKET, socket.SO_ACCEPTCONN
        ):
            parser.error("expected an accepting Unix socket")
        serve(listener, root, uid, digest)


if __name__ == "__main__":
    main()
