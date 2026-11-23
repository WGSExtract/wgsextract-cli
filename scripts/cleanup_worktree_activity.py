"""Conservative local Linux mount and task checks for worktree cleanup."""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple


def unescape_proc_path(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


class Mount(NamedTuple):
    device: str
    backing: Path
    point: Path


def mounted(path: Path) -> bool:
    with Path("/proc/self/mountinfo").open(errors="surrogateescape") as handle:
        records = [
            Mount(fields[2], *(Path(unescape_proc_path(p)) for p in fields[3:5]))
            for line in handle
            if (fields := line.split(maxsplit=5))
        ]
    if any(row.point.is_relative_to(path) for row in records):
        return True
    covering = [row for row in records if path.is_relative_to(row.point)]
    depth = max((len(row.point.parts) for row in covering), default=0)
    anchors = [row for row in covering if len(row.point.parts) == depth]
    if len(anchors) != 1:
        raise OSError("Activity unverifiable: ambiguous covering mount")
    anchor = anchors[0]
    backing = anchor.backing / path.relative_to(anchor.point)
    # Match an external mount of the checkout, a descendant, or an ancestor.
    return any(
        row.device == anchor.device
        and (
            (row.backing.is_relative_to(backing))
            or (
                backing.is_relative_to(row.backing)
                and row.point / backing.relative_to(row.backing) != path
            )
        )
        for row in records
    )


def process_paths(process: Path) -> Iterator[Path]:
    def verify_view() -> None:
        root = process / "root"
        if (
            os.readlink(process / "ns/mnt") != os.readlink("/proc/self/ns/mnt")
            or os.readlink(root) != "/"
            or not root.samefile("/")
        ):
            raise OSError(
                "Activity unverifiable: different filesystem view; use --inspector"
            )

    verify_view()
    cwd = Path(os.readlink(process / "cwd").removesuffix(" (deleted)"))
    if not cwd.is_absolute():
        raise OSError("Activity unverifiable: cwd is not absolute")
    yield cwd
    for name in ("root", "exe"):
        yield Path(os.readlink(process / name).removesuffix(" (deleted)"))
    for fd in (process / "fd").iterdir():
        try:
            yield Path(os.readlink(fd).removesuffix(" (deleted)"))
        except FileNotFoundError:
            pass
    for line in (process / "maps").read_text(errors="surrogateescape").splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            yield Path(unescape_proc_path(fields[5]).removesuffix(" (deleted)"))
    arguments = (process / "cmdline").read_bytes()
    if not arguments:
        raise FileNotFoundError("No userspace command line")
    if not arguments.endswith(b"\0"):
        raise OSError("Incomplete userspace command line")
    for arg in arguments.split(b"\0"):
        if not arg or arg.startswith(b"-"):
            continue
        path = Path(os.fsdecode(arg))
        if not path.is_absolute():
            if not (process / "cwd").samefile(cwd):
                raise OSError(
                    "Activity unverifiable: cannot verify relative argument base"
                )
            path = cwd / path
        yield Path(os.path.normpath(path))
        try:
            yield path.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            # A dangling symlink can still name a candidate.
            yield path.resolve()
        except OSError as error:
            if error.errno != errno.ENAMETOOLONG:
                raise
    verify_view()


def task_state(process: Path) -> tuple[str, bool]:
    fields = dict(
        line.split(":", 1)
        for line in (process / "status")
        .read_text(errors="surrogateescape")
        .splitlines()
        if ":" in line
    )
    state = fields.get("State", "").strip()
    kernel = fields.get("Kthread", "").strip()
    if not state or kernel not in {"", "0", "1"}:
        raise OSError("Activity unverifiable: missing task state")
    return state[0], kernel == "1"


@contextmanager
def pin(path: Path) -> Iterator[Path]:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield Path("/proc/self/fd") / str(fd)
    finally:
        os.close(fd)


def group_paths(process: Path, pid: str) -> Iterator[Path]:
    _, kernel = task_state(process)
    if kernel:
        return
    directory = process / "task"
    tasks = {task.name for task in directory.iterdir()}
    if pid not in tasks:
        raise OSError(f"Activity unverifiable for PID {pid}: missing group leader")
    for tid in sorted(tasks):
        with pin(directory / tid) as task:
            state, kernel = task_state(task)
            if not kernel and state not in {"Z", "X"}:
                yield from process_paths(task)
    if {task.name for task in directory.iterdir()} != tasks:
        raise OSError(f"Activity unverifiable for PID {pid}: task set changed")


def active_paths(proc: Path, paths: set[Path]) -> set[Path]:
    blocked: set[Path] = set()
    if not paths:
        return blocked
    with Path("/proc/self/mountinfo").open(errors="surrogateescape") as table:
        for line in table:
            before, after = line.split(" - ", 1)
            mount, filesystem = before.split(), after.split()
            if mount[4] == "/proc" and filesystem[0] == "proc":
                options = (mount[5] + "," + filesystem[2]).split(",")
                if any(
                    option.startswith(("hidepid=", "subset="))
                    and option not in {"hidepid=0", "hidepid=off"}
                    for option in options
                ):
                    raise OSError("Activity unverifiable: restrictive proc mount")
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            with pin(process) as pinned:
                try:
                    for item in group_paths(pinned, process.name):
                        blocked.update(
                            path for path in paths if item.is_relative_to(path)
                        )
                except (FileNotFoundError, ProcessLookupError):
                    # A missing task or reference is not proof the group exited.
                    task_state(pinned)
                    raise OSError(
                        f"Activity unverifiable for PID {process.name}; use --inspector"
                    ) from None
        except (FileNotFoundError, ProcessLookupError):
            continue
    return blocked
