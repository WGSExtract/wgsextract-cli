"""Read Git state and preserve detached commits before worktree retirement."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class CleanupError(Exception):
    """State could not safely be verified."""


@dataclass
class Worktree:
    path: str
    head: str
    branch: str
    locked: bool
    unavailable: bool
    detached: bool = False
    reason: str = "not-assessed"
    pr: int | None = None
    recovery: str | None = None
    archive: str | None = None
    retained_reason: str = ""
    planned_caches: list[str] = field(default_factory=list)
    pruned_caches: list[str] = field(default_factory=list)
    pruning_cache: str | None = None
    disposable_outputs: list[str] = field(default_factory=list)
    planned_outputs: list[str] = field(default_factory=list)
    pruned_outputs: list[str] = field(default_factory=list)
    pruning_output: str | None = None

    @property
    def directory(self) -> Path:
        return Path(self.path)


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
                "detached" in entries,
            )
        )
    if (
        not result
        or result[0].directory != root
        or result[0].branch != "refs/heads/main"
    ):
        raise CleanupError("Run from this repository's primary checkout on main")
    return result


def run(
    root: Path, *args: str, allow_no_matches: bool = False, stdin: bytes | None = None
) -> str:
    env = os.environ.copy()
    if args[0] == "git":
        env = {k: v for k, v in env.items() if not k.startswith("GIT_")}
        env.update(
            GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0"
        )
    result = subprocess.run(
        args,
        cwd=root,
        env=env,
        input=stdin,
        capture_output=True,
        timeout=120,
        check=False,
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


def git(
    root: Path, *args: str, allow_no_matches: bool = False, stdin: bytes | None = None
) -> str:
    return run(
        root,
        "git",
        "-c",
        "core.fsmonitor=false",
        *args,
        allow_no_matches=allow_no_matches,
        stdin=stdin,
    )


def ignored_paths(root: Path, paths: set[Path]) -> set[Path]:
    if not paths:
        return set()
    response = git(
        root,
        "check-ignore",
        "--stdin",
        "-z",
        allow_no_matches=True,
        stdin=b"".join(os.fsencode(path) + b"\0" for path in sorted(paths)),
    )
    matches = {Path(name) for name in response.split("\0")[:-1]}
    if (response and not response.endswith("\0")) or not matches <= paths:
        raise CleanupError("Git returned unexpected ignored paths")
    return matches


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


def hook_paths(location: Path) -> tuple[Path, Path]:
    keys = git(
        location,
        "config",
        "--null",
        "--name-only",
        "--get-regexp",
        r"^hook\..*\.command$",
        allow_no_matches=True,
    )
    if keys:
        key = keys.split("\0", 1)[0]
        raise CleanupError(f"config-hook-unverifiable: {key}")
    git_dir = Path(git(location, "rev-parse", "--absolute-git-dir").removesuffix("\n"))
    # Git uses the checkout or $GIT_DIR as the hook working directory.
    reported = git(location, "rev-parse", "--git-path", "hooks").removesuffix("\n")
    if not git_dir.is_absolute() or not reported:
        raise CleanupError("Git did not report its hooks or metadata path")
    return location / reported, git_dir / reported


def explicit_crlf_checkout(path: Path, relative: Path) -> bool:
    name = str(relative)
    entries = git(path, "check-attr", "--cached", "-z", "--all", "--", name).split(
        "\0"
    )[:-1]
    attributes = {}
    for reported, attribute, setting in zip(
        entries[::3], entries[1::3], entries[2::3], strict=True
    ):
        if reported != name:
            raise CleanupError("Git returned attributes for an unexpected path")
        attributes[attribute] = setting
    return (
        attributes.get("text") == "set"
        and attributes.get("eol") == "crlf"
        and not attributes.keys() & {"filter", "ident", "working-tree-encoding"}
    )


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
