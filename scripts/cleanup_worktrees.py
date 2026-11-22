#!/usr/bin/env python3
"""Audit stale WGS Extract worktrees on Linux; remove nothing by default."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import cleanup_worktree_inventory as inventory_files
else:
    # Isolated mode excludes the checkout from the import path. Execute only
    # the checked helper bytes, rather than searching that path for imports.
    source = Path(__file__).with_name("cleanup_worktree_inventory.py")
    code = source.read_bytes()
    if hashlib.sha256(code).hexdigest() != (
        "b860929b5bbdf610de1005a88401b1a2d84f97300d3564d952bda31c087d4558"
    ):
        raise SystemExit("Worktree inventory helper version mismatch")
    inventory_files = ModuleType("wgse_worktree_inventory")
    sys.modules[inventory_files.__name__] = inventory_files
    exec(compile(code, str(source), "exec"), vars(inventory_files))

REPO = "WGSExtract/wgsextract-cli"
LEGACY_REPO = "theontho/wgsextract-cli"
PROC = Path("/proc")
INSPECTOR_SOCKET = Path("/run/wgse-worktree-inspector.sock")
INSPECTOR_UID = 0
INSPECTOR_SHA256 = "33f456ce89a2c096bf5a4110721621fcaf767cf9fa7aa11d207d328b21354402"
MIN_MERGE_AGE = timedelta(days=1)
PR_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    pullRequests(first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number state mergedAt author { login }
        baseRefName headRefName headRefOid headRepository { nameWithOwner }
      }
    }
  }
}
"""


class CleanupError(Exception):
    """State could not safely be verified."""


ERRORS = (
    CleanupError,
    OSError,
    ValueError,
    KeyError,
    TypeError,
    RuntimeError,
    subprocess.TimeoutExpired,
)


@dataclass
class Worktree:
    path: str
    head: str
    branch: str
    locked: bool
    unavailable: bool
    reason: str = "not-assessed"
    pr: int | None = None
    archive: str | None = None
    retained_reason: str = ""
    planned_caches: list[str] = field(default_factory=list)
    pruned_caches: list[str] = field(default_factory=list)
    pruning_cache: str | None = None

    @property
    def directory(self) -> Path:
        return Path(self.path)


def run(root: Path, *args: str) -> str:
    env = os.environ.copy()
    if args[0] == "git":
        env = {k: v for k, v in env.items() if not k.startswith("GIT_")}
        env.update(GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        args, cwd=root, env=env, capture_output=True, timeout=120, check=False
    )
    if result.returncode:
        raise CleanupError(
            f"{args[0]} {args[1]} failed ({result.returncode}): "
            f"{os.fsdecode(result.stderr).strip()}"
        )
    return os.fsdecode(result.stdout)


def git(root: Path, *args: str) -> str:
    return run(root, "git", "-c", "core.fsmonitor=false", *args)


def worktrees(root: Path) -> list[Worktree]:
    result = []
    for record in git(root, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        if not record:
            continue
        entries = dict(line.partition(" ")[::2] for line in record.split("\0"))
        path = entries["worktree"]
        if not Path(path).is_absolute():
            raise CleanupError("Git reported a non-absolute worktree path")
        result.append(
            Worktree(
                path,
                entries.get("HEAD", ""),
                entries.get("branch", ""),
                "locked" in entries,
                "prunable" in entries or "bare" in entries,
            )
        )
    if (
        not result
        or result[0].directory != root
        or result[0].branch != "refs/heads/main"
    ):
        raise CleanupError("Run from this repository's primary checkout on main")
    return result


def safe_directory(path: Path, mode: int = 0o755) -> None:
    path.mkdir(mode=mode, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
        or (mode == 0o700 and info.st_mode & 0o077)
    ):
        raise CleanupError(f"Unsafe output directory: {path}")


def candidate(root: Path, tree: Worktree, trees: list[Worktree]) -> str:
    path = tree.directory
    if path.parent != root / "scratch" or path.resolve() != path:
        return "outside-canonical-scratch"
    if tree.locked:
        return "git-locked"
    if tree.unavailable or not path.is_dir():
        return "unavailable"
    if not tree.branch.startswith("refs/heads/") or tree.branch == "refs/heads/main":
        return "not-a-topic-branch"
    if any(
        other.directory != path and other.directory.resolve().is_relative_to(path)
        for other in trees
    ):
        return "contains-registered-worktree"
    return ""


def link_blocks(root: Path, trees: list[Worktree], paths: set[Path]) -> set[Path]:
    if not paths:
        return set()
    # The primary walk includes registered worktrees under the repository.
    locations = {root}
    for tree in trees:
        location = tree.directory.resolve()
        if not location.is_relative_to(root) and location.is_dir():
            locations.add(location)
    blocked = set()
    for location in locations:
        for entry in inventory_files.entries(location, {root / ".git"}):
            if not entry.is_symlink():
                continue
            link = Path(entry.path)
            target = link.parent / os.readlink(link)
            lexical, resolved = Path(os.path.abspath(target)), target.resolve()
            for path in paths:
                inside = link.is_relative_to(path)
                if inside != resolved.is_relative_to(path) or (
                    not inside
                    and (
                        lexical.is_relative_to(path)
                        or (
                            resolved != root
                            and path.is_relative_to(resolved)
                            and resolved.is_relative_to(root / "scratch")
                        )
                    )
                ):
                    blocked.add(path)
    return blocked


def unescape_proc_path(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def mounted(path: Path) -> bool:
    with Path("/proc/self/mountinfo").open(errors="surrogateescape") as handle:
        return any(
            Path(unescape_proc_path(line.split()[4])).is_relative_to(path)
            for line in handle
        )


def process_paths(process: Path) -> Iterator[Path]:
    cwd = None
    for name in ("cwd", "root", "exe"):
        try:
            path = Path(os.readlink(process / name).removesuffix(" (deleted)"))
        except FileNotFoundError:
            continue
        if name == "cwd":
            cwd = path
        yield path
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
            if cwd is None:
                continue
            path = cwd / path
        yield Path(os.path.normpath(path))


def inactive_group(process: Path, pid: str) -> bool:
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
        raise OSError(f"Activity unverifiable for PID {pid}: missing process state")
    if kernel == "1":
        return True
    if state[0] not in {"Z", "X"}:
        return False
    try:
        if [thread.name for thread in (process / "task").iterdir()] != [pid]:
            raise OSError(f"Activity unverifiable for PID {pid}: surviving threads")
    except (FileNotFoundError, ProcessLookupError):
        # Missing task metadata is only proof of exit if this pinned PID is gone.
        (process / "status").read_bytes()
        raise OSError(
            f"Activity unverifiable for PID {pid}: missing task state"
        ) from None
    return True


def active_paths(paths: set[Path]) -> set[Path]:
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
    for process in PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            fd = os.open(process, os.O_RDONLY | os.O_DIRECTORY)
            try:
                pinned = Path("/proc/self/fd") / str(fd)
                if not inactive_group(pinned, process.name):
                    try:
                        for item in process_paths(pinned):
                            blocked.update(
                                path for path in paths if item.is_relative_to(path)
                            )
                    except (FileNotFoundError, ProcessLookupError):
                        if not inactive_group(pinned, process.name):
                            raise OSError(
                                f"Activity unverifiable for PID {process.name}"
                            ) from None
                    inactive_group(pinned, process.name)
            finally:
                os.close(fd)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return blocked


def inspected_paths(root: Path, paths: set[Path]) -> set[Path]:
    def identity(path: Path) -> tuple[int, int]:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("Inspector candidate changed")
        return info.st_dev, info.st_ino

    if not paths:
        return set()
    parent = INSPECTOR_SOCKET.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != INSPECTOR_UID
        or parent.st_mode & 0o022
    ):
        raise OSError("Inspector socket parent is not administrator-controlled")
    ordered = sorted(paths)
    blocked = set()
    repo_id = identity(root)
    for offset in range(0, len(ordered), 64):
        batch = {path.name: path for path in ordered[offset : offset + 64]}
        if any(path.parent != root / "scratch" for path in batch.values()):
            raise OSError("Invalid inspector candidate path")
        candidates = {name: identity(path) for name, path in batch.items()}
        request = {"repository": repo_id, "candidates": candidates}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(45)
            connection.connect(str(INSPECTOR_SOCKET))
            _, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != INSPECTOR_UID:
                raise OSError("Inspector socket is not owned by the administrator")
            connection.sendall(json.dumps(request).encode() + b"\n")
            with connection.makefile("rb") as stream:
                response = stream.readline(16_385)
        if not response.endswith(b"\n") or len(response) > 16_384:
            raise OSError("Incomplete inspector reply")
        try:
            reply = json.loads(response)
        except (ValueError, RecursionError) as exc:
            raise OSError("Invalid inspector reply") from exc
        if isinstance(reply, dict) and reply.get("error") == "unverifiable":
            pid = reply.get("pid")
            detail = f" (PID {pid})" if type(pid) is int and 0 < pid < 2**31 else ""
            raise OSError(f"Worktree activity unverifiable{detail}")
        if (
            not isinstance(reply, dict)
            or reply.keys() != {"sha256", "busy"}
            or reply["sha256"] != INSPECTOR_SHA256
            or not isinstance(busy := reply["busy"], dict)
            or busy.keys() != batch.keys()
        ):
            raise OSError(
                "Worktree activity unverifiable or inspector version mismatch"
            )
        for name, pids in busy.items():
            if not isinstance(pids, list) or any(
                type(pid) is not int or pid <= 0 for pid in pids
            ):
                raise OSError("Invalid inspector activity result")
            if identity(batch[name]) != candidates[name]:
                raise OSError("Inspector candidate changed")
            if pids:
                blocked.add(batch[name])
    return blocked


def contents(path: Path) -> inventory_files.Inventory:
    if git(
        path,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ):
        return inventory_files.Inventory(blocked="dirty-or-untracked")
    index = git(path, "ls-files", "-v", "-z").split("\0")[:-1]
    if any(not entry.startswith("H ") for entry in index):
        return inventory_files.Inventory(blocked="special-index-state")
    tracked = {Path(entry[2:]) for entry in index}
    return inventory_files.inventory(path, tracked)


def check_pull_requests(root: Path, trees: list[Worktree]) -> None:
    if not trees:
        return
    owner, name = REPO.split("/")
    # REST's head filter omits merged PRs whose remote branches were deleted.
    # Fetch only decision metadata and match branches locally.
    response = json.loads(
        run(
            root,
            "gh",
            "api",
            "--hostname",
            "github.com",
            "graphql",
            "--paginate",
            "--slurp",
            "-f",
            f"query={PR_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
        )
    )
    if not isinstance(response, list) or not response:
        raise CleanupError("Unexpected GitHub pull-request response")
    login = response[0]["data"]["viewer"]["login"]
    if not isinstance(login, str) or not re.fullmatch(r"[a-zA-Z0-9-]+", login):
        raise CleanupError("Could not verify the authenticated GitHub account")
    latest: dict[str, tuple[int, dict[str, object]]] = {}
    for index, page in enumerate(response):
        repo = page["data"]["repository"]
        if (
            page.get("errors")
            or repo["nameWithOwner"] != REPO
            or page["data"]["viewer"]["login"] != login
        ):
            raise CleanupError("Could not verify GitHub repository and account")
        pulls = repo["pullRequests"]
        if pulls["pageInfo"]["hasNextPage"] is not (index + 1 < len(response)):
            raise CleanupError("Incomplete GitHub pull-request history")
        for pr in pulls["nodes"]:
            head_repo = pr["headRepository"]
            if head_repo is None or head_repo["nameWithOwner"] != REPO:
                continue
            branch, number = pr["headRefName"], pr["number"]
            if not isinstance(branch, str) or type(number) is not int:
                raise CleanupError("Invalid GitHub pull request metadata")
            if branch not in latest or number > latest[branch][0]:
                latest[branch] = number, pr
    for tree in trees:
        branch = tree.branch.removeprefix("refs/heads/")
        if branch not in latest:
            tree.reason = "no-pull-request"
        else:
            tree.pr, pr = latest[branch]
            tree.reason = pr_reason(tree, pr, login)


def pr_reason(tree: Worktree, latest: dict[str, object], login: str) -> str:
    author = latest["author"]
    if not isinstance(author, dict) or author["login"] != login:
        return "pull-request-not-owned"
    merged = latest["mergedAt"]
    if (
        latest["state"] != "MERGED"
        or not isinstance(merged, str)
        or latest["baseRefName"] != "main"
    ):
        return "latest-pull-request-not-merged-to-main"
    if latest["headRefOid"] != tree.head:
        return "head-not-exact-merged-head"
    merged_at = datetime.fromisoformat(merged.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - merged_at < MIN_MERGE_AGE:
        return "recently-merged"
    return "eligible"


def archive_evidence(
    root: Path, tree: Worktree, evidence: dict[Path, inventory_files.Stamp], run_id: str
) -> None:
    if not evidence:
        return
    safe_directory(root / "out" / "archive")
    destination = root / "out" / "archive" / f"{tree.directory.name}-{run_id}"
    destination.mkdir(mode=0o700)
    tree.archive = str(destination.relative_to(root))
    for relative, expected in sorted(evidence.items()):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(
                inventory_files.read_evidence(tree.directory, relative, expected)
            )


def available_caches(
    tree: Worktree, inventory: inventory_files.Inventory, links: set[Path]
) -> list[str]:
    return [name for name in inventory.caches if tree.directory / name not in links]


def plan(
    root: Path, trees: list[Worktree], github: bool, inspector: bool, trim_caches: bool
) -> None:
    paths = set()
    inventories: dict[Path, inventory_files.Inventory] = {}
    for tree in trees[1:]:
        tree.reason = candidate(root, tree, trees)
        if not tree.reason and mounted(tree.directory):
            tree.reason = "contains-mount"
        if not tree.reason:
            inventory = contents(tree.directory)
            tree.reason = inventory.blocked
            if not tree.reason:
                inventories[tree.directory] = inventory
                if not inventory.retained:
                    paths.add(tree.directory)
                if trim_caches:
                    paths.update(tree.directory / name for name in inventory.caches)
    links = link_blocks(root, trees, paths)
    candidates = []
    for tree in trees[1:]:
        if tree.reason:
            continue
        inventory = inventories[tree.directory]
        tree.retained_reason = inventory.retained or (
            "symlink-dependency" if tree.directory in links else ""
        )
        if trim_caches:
            tree.planned_caches = available_caches(tree, inventory, links)
        if not tree.retained_reason or tree.planned_caches:
            tree.reason = "github-not-checked"
            candidates.append(tree)
        else:
            tree.reason = tree.retained_reason
    if github:
        check_pull_requests(root, candidates)
    pending_activity_check = [
        tree for tree in candidates if tree.reason in {"eligible", "github-not-checked"}
    ]
    try:
        paths = {tree.directory for tree in pending_activity_check}
        active = inspected_paths(root, paths) if inspector else active_paths(paths)
    except OSError:
        for tree in pending_activity_check:
            tree.reason = "activity-unverified"
        raise
    for tree in pending_activity_check:
        if tree.directory in active:
            tree.reason = "active-process"
        elif tree.reason == "eligible" and tree.retained_reason:
            tree.reason = "cache-eligible"


def recheck(root: Path, tree: Worktree, inspector: bool) -> str:
    trees = worktrees(root)
    now = next((item for item in trees if item.path == tree.path), None)
    if now is None or now.head != tree.head or now.branch != tree.branch:
        return "registration-changed"
    if reason := candidate(root, now, trees):
        return reason
    if mounted(tree.directory):
        return "contains-mount"
    paths = {tree.directory}
    active = inspected_paths(root, paths) if inspector else active_paths(paths)
    if active:
        return "active-process"
    return ""


def apply_tree(
    root: Path, tree: Worktree, inspector: bool, run_id: str, save: Callable[[], None]
) -> str:
    path = tree.directory
    inventory = contents(path)
    if inventory.blocked:
        return inventory.blocked
    keep = bool(tree.retained_reason)
    if not keep and inventory.retained:
        return inventory.retained
    tree.reason = "cache-prune-in-progress" if keep else "removal-in-progress"
    save()
    archive_evidence(root, tree, inventory.evidence, run_id)
    save()
    if inventory != contents(path):
        return "changed-during-archive"
    paths = {path / name for name in tree.planned_caches} if keep else {path}
    links = link_blocks(root, worktrees(root), paths)
    if reason := recheck(root, tree, inspector):
        return reason
    if not keep:
        if links:
            return "symlink-dependency"
        git(root, "worktree", "remove", "--", tree.path)
        return "removed"
    for name in tree.planned_caches:
        if path / name in links or name not in inventory.caches:
            continue
        if not inventory_files.verified_cache(path, name):
            raise CleanupError(f"Cache contents changed before removal: {name}")
        if reason := recheck(root, tree, inspector):
            return reason
        tree.pruning_cache = name
        save()
        inventory_files.prune_cache(path, name, inventory.caches[name])
        tree.pruned_caches.append(name)
        tree.pruning_cache = None
        save()
    return "cache-pruned" if tree.pruned_caches else "caches-changed-or-linked"


def cleanup(
    root: Path,
    report: Path,
    *,
    apply: bool,
    github: bool,
    inspector: bool,
    trim_caches: bool,
) -> None:
    trees = worktrees(root)
    trees[0].reason = "primary-checkout"
    data: dict[str, object] = {
        "apply": apply,
        "github": github,
        "inspector": inspector,
        "trim_caches": trim_caches,
    }

    def save() -> None:
        data["worktrees"] = [asdict(tree) for tree in trees]
        staging = report.with_suffix(".pending")
        with staging.open("x") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        staging.replace(report)

    try:
        plan(root, trees, github, inspector, trim_caches)
        save()
        if apply:
            eligible = [
                tree for tree in trees if tree.reason in {"eligible", "cache-eligible"}
            ]
            for tree in eligible:
                tree.reason = recheck(root, tree, inspector)
                if not tree.reason:
                    tree.reason = apply_tree(root, tree, inspector, report.stem, save)
                save()
    except ERRORS as exc:
        data["error"] = str(exc)
        save()
        raise
    print(
        f"{sum(tree.reason == 'removed' for tree in trees)} removed; "
        f"{sum(bool(tree.pruned_caches) for tree in trees)} cache-trimmed; "
        f"{sum(tree.reason in {'eligible', 'cache-eligible'} for tree in trees)} verified eligible; "
        f"{sum(not tree.pruned_caches and tree.reason not in {'removed', 'eligible', 'cache-eligible'} for tree in trees[1:])} skipped. "
        f"Report: {report.relative_to(root)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="remove verified eligible worktrees"
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="request this repository's PR metadata from github.com (match branches locally)",
    )
    parser.add_argument(
        "--inspector",
        action="store_true",
        help="use the explicitly installed local read-only activity inspector; never fall back",
    )
    parser.add_argument(
        "--trim-caches",
        action="store_true",
        help="also trim verified generated caches in retained merged worktrees",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.github:
        parser.error("--apply requires --github to verify merged PRs")
    report = None
    try:
        if sys.platform != "linux":
            raise CleanupError("Cleanup requires Linux process and mount inspection")
        root = Path.cwd().resolve()
        origin = git(root, "remote", "get-url", "origin").strip().removesuffix(".git")
        if origin.lower() not in {
            f"{prefix}{repository}".lower()
            for prefix in (
                "https://github.com/",
                "git@github.com:",
                "ssh://git@github.com/",
            )
            for repository in (REPO, LEGACY_REPO)
        }:
            raise CleanupError(f"Expected origin for {REPO}")
        common = Path(
            git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        )
        if common != root / ".git" or common.resolve() != common:
            raise CleanupError("Run from this repository's primary checkout")
        lock_fd = os.open(
            common / "cleanup-worktrees.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(lock_fd, "rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CleanupError(
                    "Another worktree cleanup holds the repository lock"
                ) from exc
            safe_directory(root / "out")
            reports = root / "out" / "worktree-cleanup"
            safe_directory(reports, 0o700)
            run_id = (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid.uuid4().hex[:8]
            )
            report = reports / f"{run_id}.json"
            cleanup(
                root,
                report,
                apply=args.apply,
                github=args.github,
                inspector=args.inspector,
                trim_caches=args.trim_caches,
            )
        return 0
    except ERRORS as exc:
        print(f"Worktree cleanup failed: {exc}", file=sys.stderr)
        if report is not None:
            print(f"Report: {report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
