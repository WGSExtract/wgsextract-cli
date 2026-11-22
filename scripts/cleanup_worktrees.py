#!/usr/bin/env python3
"""Audit stale WGS Extract worktrees on Linux; remove nothing by default."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "WGSExtract/wgsextract-cli"
LEGACY_REPO = "theontho/wgsextract-cli"
PROC = Path("/proc")
CACHE_DIRS = {
    ".pixi/envs",
    ".pixi/task-cache-v0",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
CACHE_MARKERS = {Path(".pixi/.gitignore"), Path(".pixi/.condapackageignore")}
TEXT_SUFFIXES = {".log", ".txt", ".md", ".json", ".csv", ".tsv", ".xml"}
SENSITIVE_NAME = re.compile(
    r"(^|[^a-z0-9])(secret|credential|password|token|key|env|config)s?($|[^a-z0-9])",
    re.I,
)
MAX_FILE = 1024 * 1024
MAX_ARCHIVE = 32 * MAX_FILE
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

    @property
    def directory(self) -> Path:
        return Path(self.path)


Stamp = tuple[int, int, int, int, int]


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


def entries(path: Path, excluded: set[Path]) -> Iterator[os.DirEntry[str]]:
    todo = [path]
    while todo:
        directory = todo.pop()
        with os.scandir(directory) as children:
            for entry in children:
                child = Path(entry.path)
                if child in excluded:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    todo.append(child)
                else:
                    yield entry


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
        for entry in entries(location, {root / ".git"}):
            if not entry.is_symlink():
                continue
            link = Path(entry.path)
            target = link.parent / os.readlink(link)
            lexical, resolved = Path(os.path.abspath(target)), target.resolve()
            for path in paths:
                inside = link.is_relative_to(path)
                if inside != resolved.is_relative_to(path) or (
                    not inside and lexical.is_relative_to(path)
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
    for name in ("cwd", "root", "exe"):
        try:
            yield Path(os.readlink(process / name).removesuffix(" (deleted)"))
        except FileNotFoundError:
            pass
    for fd in (process / "fd").iterdir():
        try:
            yield Path(os.readlink(fd).removesuffix(" (deleted)"))
        except FileNotFoundError:
            pass
    for line in (process / "maps").read_text(errors="surrogateescape").splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            yield Path(unescape_proc_path(fields[5]).removesuffix(" (deleted)"))
    for arg in (process / "cmdline").read_bytes().split(b"\0"):
        if arg.startswith(b"/"):
            yield Path(os.fsdecode(arg))


def active_paths(paths: set[Path]) -> set[Path]:
    blocked: set[Path] = set()
    if not paths:
        return blocked
    for process in PROC.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            # Unprivileged cleanup can only inspect other processes of its own UID.
            if process.stat().st_uid == os.geteuid():
                for item in process_paths(process):
                    blocked.update(path for path in paths if item.is_relative_to(path))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return blocked


def stamp(info: os.stat_result) -> Stamp:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_evidence(directory: Path, relative: Path, expected: Stamp) -> bytes:
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        parent = os.open(directory, dir_flags)
        stack.callback(os.close, parent)
        for component in relative.parts[:-1]:
            parent = os.open(component, dir_flags, dir_fd=parent)
            stack.callback(os.close, parent)
        fd = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        handle = stack.enter_context(os.fdopen(fd, "rb"))
        if stamp(os.fstat(handle.fileno())) != expected:
            raise CleanupError(f"Evidence changed: {relative}")
        data = handle.read(MAX_FILE + 1)
        if stamp(os.fstat(handle.fileno())) != expected:
            raise CleanupError(f"Evidence changed: {relative}")
    return data


def contents(path: Path) -> tuple[str, dict[Path, Stamp]]:
    if git(
        path,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ):
        return "dirty-or-untracked", {}
    index = git(path, "ls-files", "-v", "-z").split("\0")[:-1]
    if any(not entry.startswith("H ") for entry in index):
        return "special-index-state", {}
    tracked = {Path(entry[2:]) for entry in index}
    evidence: dict[Path, Stamp] = {}
    size = 0
    excluded = {
        path / name
        for name in CACHE_DIRS
        if (path / name).is_dir() and not (path / name).is_symlink()
    }
    for entry in entries(path, excluded | {path / ".git"}):
        relative = Path(entry.path).relative_to(path)
        if relative in tracked:
            continue
        if (
            relative.parent.name == "__pycache__"
            and relative.suffix == ".pyc"
            and entry.is_file(follow_symlinks=False)
        ):
            continue
        if not entry.is_file(follow_symlinks=False):
            return f"unknown-data: {relative}", {}
        info = entry.stat(follow_symlinks=False)
        if relative in CACHE_MARKERS:
            continue
        if (
            relative
            in {
                Path("out/.metadata_never_index"),
                Path("tmp/.metadata_never_index"),
                Path("scratch/.metadata_never_index"),
            }
            and info.st_size == 0
        ):
            continue
        if (
            relative.parts[0] != "out"
            or relative.suffix not in TEXT_SUFFIXES
            or any(SENSITIVE_NAME.search(part) for part in relative.parts)
        ):
            return f"unknown-data: {relative}", {}
        size += info.st_size
        if info.st_size > MAX_FILE or size > MAX_ARCHIVE:
            return f"evidence-too-large: {relative}", {}
        if info.st_uid != os.geteuid() or info.st_nlink != 1:
            return f"unsafe-evidence: {relative}", {}
        expected = stamp(info)
        data = read_evidence(path, relative, expected)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return f"non-text-evidence: {relative}", {}
        if b"\0" in data:
            return f"non-text-evidence: {relative}", {}
        evidence[relative] = expected
    return "", evidence


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
    root: Path, tree: Worktree, evidence: dict[Path, Stamp], run_id: str
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
            handle.write(read_evidence(tree.directory, relative, expected))


def plan(root: Path, trees: list[Worktree], github: bool) -> None:
    paths = set()
    for tree in trees[1:]:
        tree.reason = candidate(root, tree, trees)
        if not tree.reason and mounted(tree.directory):
            tree.reason = "contains-mount"
        if not tree.reason:
            tree.reason, _ = contents(tree.directory)
        if not tree.reason:
            paths.add(tree.directory)
    links = link_blocks(root, trees, paths)
    candidates = []
    for tree in trees[1:]:
        if tree.reason:
            continue
        if tree.directory in links:
            tree.reason = "symlink-dependency"
        else:
            tree.reason = "github-not-checked"
            candidates.append(tree)
    if github:
        check_pull_requests(root, candidates)
    pending_activity_check = [
        tree for tree in candidates if tree.reason in {"eligible", "github-not-checked"}
    ]
    try:
        active = active_paths({tree.directory for tree in pending_activity_check})
    except OSError:
        for tree in pending_activity_check:
            tree.reason = "activity-unverified"
        raise
    for tree in pending_activity_check:
        if tree.directory in active:
            tree.reason = "active-process"


def recheck(root: Path, tree: Worktree) -> str:
    trees = worktrees(root)
    now = next((item for item in trees if item.path == tree.path), None)
    if now is None or now.head != tree.head or now.branch != tree.branch:
        return "registration-changed"
    if reason := candidate(root, now, trees):
        return reason
    if mounted(tree.directory):
        return "contains-mount"
    if active_paths({tree.directory}):
        return "active-process"
    return ""


def cleanup(root: Path, report: Path, *, apply: bool, github: bool) -> None:
    trees = worktrees(root)
    trees[0].reason = "primary-checkout"
    data: dict[str, object] = {"apply": apply, "github": github}

    def save() -> None:
        data["worktrees"] = [asdict(tree) for tree in trees]
        staging = report.with_suffix(".pending")
        with staging.open("x") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        staging.replace(report)

    try:
        plan(root, trees, github)
        save()
        if apply:
            eligible = [tree for tree in trees if tree.reason == "eligible"]
            for tree in eligible:
                tree.reason = recheck(root, tree)
                if not tree.reason:
                    tree.reason, evidence = contents(tree.directory)
                    if not tree.reason:
                        tree.reason = "removal-in-progress"
                        archive_evidence(root, tree, evidence, report.stem)
                        save()
                        if link_blocks(root, worktrees(root), {tree.directory}):
                            tree.reason = "symlink-dependency"
                        elif contents(tree.directory) != ("", evidence):
                            tree.reason = "changed-during-archive"
                        elif reason := recheck(root, tree):
                            tree.reason = reason
                        else:
                            git(root, "worktree", "remove", "--", tree.path)
                            tree.reason = "removed"
                save()
    except ERRORS as exc:
        data["error"] = str(exc)
        save()
        raise
    print(
        f"{sum(tree.reason == 'removed' for tree in trees)} removed; "
        f"{sum(tree.reason == 'eligible' for tree in trees)} verified eligible; "
        f"{sum(tree.reason not in {'removed', 'eligible'} for tree in trees[1:])} skipped. "
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
            cleanup(root, report, apply=args.apply, github=args.github)
        return 0
    except ERRORS as exc:
        print(f"Worktree cleanup failed: {exc}", file=sys.stderr)
        if report is not None:
            print(f"Report: {report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
