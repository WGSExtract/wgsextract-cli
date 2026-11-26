"""Read Git state and preserve detached commits before worktree retirement."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path


class CleanupError(Exception):
    """State could not safely be verified."""


def run(root: Path, *args: str, allow_no_matches: bool = False) -> str:
    env = os.environ.copy()
    if args[0] == "git":
        env = {k: v for k, v in env.items() if not k.startswith("GIT_")}
        env.update(
            GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0"
        )
    result = subprocess.run(
        args, cwd=root, env=env, capture_output=True, timeout=120, check=False
    )
    if (
        allow_no_matches
        and result.returncode == 1
        and not (result.stdout or result.stderr)
    ):
        return ""
    if result.returncode or (allow_no_matches and result.stderr):
        raise CleanupError(
            f"{args[0]} {args[1]} failed ({result.returncode}): "
            f"{os.fsdecode(result.stderr).strip()}"
        )
    return os.fsdecode(result.stdout)


def git(root: Path, *args: str, allow_no_matches: bool = False) -> str:
    return run(
        root,
        "git",
        "-c",
        "core.fsmonitor=false",
        *args,
        allow_no_matches=allow_no_matches,
    )


def operation_in_progress(path: Path) -> bool:
    admin = Path(git(path, "rev-parse", "--absolute-git-dir").removesuffix("\n"))
    for marker in (
        "HEAD.lock",
        "index.lock",
        "BISECT_START",
        "CHERRY_PICK_HEAD",
        "MERGE_HEAD",
        "REBASE_HEAD",
        "REVERT_HEAD",
        "rebase-apply",
        "rebase-merge",
        "sequencer",
    ):
        try:
            (admin / marker).lstat()
        except FileNotFoundError:
            continue
        return True
    return False


MAIN_REF = "refs/remotes/origin/main"


def origin_main(root: Path) -> str:
    return git(
        root,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{MAIN_REF}^{{commit}}",
        allow_no_matches=True,
    ).strip()


def on_main(root: Path, head: str, main: str) -> bool:
    return (
        bool(main)
        and git(root, "merge-base", head, main, allow_no_matches=True).strip() == head
    )


def detached_ref(head: str) -> str:
    return f"refs/worktree-cleanup/detached/{head}"


def reflog_tips(path: Path, oid_len: int) -> set[str] | None:
    storage = git(
        path, "config", "--get", "extensions.refStorage", allow_no_matches=True
    ).strip()
    if storage not in {"", "files"}:
        return None
    log = git(
        path, "rev-parse", "--path-format=absolute", "--git-path", "logs/HEAD"
    ).removesuffix("\n")
    try:
        fd = os.open(log, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return set()
    tips: set[str] = set()
    oid = re.compile(rb"[0-9a-f]{%d}" % oid_len)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        for count, line in enumerate(iter(lambda: handle.readline(65537), b"")):
            old, _, remainder = line.partition(b" ")
            new, separator, _ = remainder.partition(b" ")
            if (
                count >= 1024
                or not line.endswith(b"\n")
                or not separator
                or not (oid.fullmatch(old) and oid.fullmatch(new))
            ):
                return None
            tips.update(value.decode("ascii") for value in (old, new) if int(value, 16))
    return tips


def has_unpreserved_reflog(path: Path, head: str) -> bool:
    # Git's reflog show omits old OIDs even when they are the only copy.
    tips = reflog_tips(path, len(head))
    if tips is None:
        return True
    if original := git(
        path, "rev-parse", "--verify", "--quiet", "ORIG_HEAD", allow_no_matches=True
    ).strip():
        tips.add(original)
    tips.discard(head)
    if not tips:
        return False
    main = origin_main(path)
    return bool(
        git(
            path,
            "rev-list",
            "--max-count=1",
            *sorted(tips),
            "--not",
            head,
            "--branches",
            "--tags",
            "--glob=refs/worktree-cleanup/detached/*",
            *([main] if main else []),
            allow_no_matches=True,
        )
    )


def detached_block(path: Path, head: str, branch: str) -> str:
    if branch or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head):
        return "unverified-detached-head"
    if operation_in_progress(path):
        return "git-operation-in-progress"
    if has_unpreserved_reflog(path, head):
        return "detached-reflog-unpreserved"
    if git(path, "symbolic-ref", "--quiet", "HEAD", allow_no_matches=True) or (
        git(path, "rev-parse", "--verify", "HEAD").strip() != head
    ):
        return "registration-changed"
    return ""


def preserve_detached_head(
    root: Path, head: str, recovery: str | None, *, from_main: bool
) -> bool:
    if recovery != detached_ref(head):
        raise CleanupError("No recovery ref verified for detached HEAD")
    if from_main and not on_main(root, head, origin_main(root)):
        return False
    # Keep an exact recovery target, even if origin/main advances or the
    # commit is reachable only through the PR/worktree.
    existing = git(
        root, "for-each-ref", "--format=%(refname) %(objectname)%(symref)", recovery
    )
    if existing != f"{recovery} {head}\n":
        git(
            root,
            "-c",
            "core.hooksPath=/dev/null",
            "update-ref",
            "--no-deref",
            recovery,
            head,
            "0" * len(head),
        )
    return True
