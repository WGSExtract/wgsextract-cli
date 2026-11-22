"""Inspect and prune only named, recognizably generated worktree caches."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

CACHE_DIRS = {
    ".pixi/envs",
    ".pixi/task-cache-v0",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
CACHE_MARKERS = {
    Path(".pixi/.gitignore"): b"*\n!config.toml\n",
    Path(".pixi/.condapackageignore"): b".pixi\n!.pixi/config.toml\n",
}
CACHE_TAG = b"Signature: 8a477f597d28d172789f06886806bc55"
TEXT_SUFFIXES = {".log", ".txt", ".md", ".json", ".csv", ".tsv", ".xml"}
SCRATCH_SOURCE_SUFFIXES = {".py", ".sh", ".lua", ".luau", ".r", ".R"}
SENSITIVE_NAME = re.compile(
    r"(^|[^a-z0-9])(secret|credential|password|token|key|env|config)s?($|[^a-z0-9])",
    re.I,
)
MAX_FILE = 1024 * 1024
MAX_ARCHIVE = 32 * MAX_FILE

Stamp = tuple[int, int, int, int, int]


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
            raise OSError(f"Evidence changed: {relative}")
        data = handle.read(MAX_FILE + 1)
        if stamp(os.fstat(handle.fileno())) != expected:
            raise OSError(f"Evidence changed: {relative}")
    return data


def cache_metadata(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ValueError("Unexpected cache metadata")
        return handle.read(MAX_ARCHIVE + 1)


def cache_tagged(path: Path) -> bool:
    return cache_metadata(path / "CACHEDIR.TAG").partition(b"\n")[0] == CACHE_TAG


def pixi_environment(path: Path, tree: Path, locked: set[bytes]) -> bool:
    metadata = path / "conda-meta"
    if metadata.is_symlink() or not cache_tagged(path):
        return False
    pixi = json.loads(cache_metadata(metadata / "pixi"))
    if (
        not isinstance(pixi, dict)
        or pixi.get("environment_name") != path.name
        or pixi.get("manifest_path")
        not in {
            str(tree / "pixi.toml"),
            str(tree / "pyproject.toml"),
        }
        or not isinstance(pixi.get("pixi_version"), str)
        or not isinstance(pixi.get("environment_lock_file_hash"), (int, str))
    ):
        return False
    known = {
        Path("CACHEDIR.TAG"),
        Path("conda-meta/pixi"),
        Path("conda-meta/history"),
        Path("conda-meta/pixi_env_prefix"),
        Path("conda-meta/.pixi-environment-fingerprint"),
    }
    records = list(metadata.glob("*.json"))
    if not records:
        return False
    for record in records:
        package = json.loads(cache_metadata(record))
        if (
            not isinstance(package, dict)
            or not all(
                isinstance(package.get(key), str)
                for key in ("name", "version", "build")
            )
            or not isinstance(url := package.get("url"), str)
            or url.encode() not in locked
            or record.stem
            != f"{package['name']}-{package['version']}-{package['build']}"
            or not isinstance(installed := package.get("files"), list)
            or not all(isinstance(name, str) for name in installed)
        ):
            return False
        known.add(record.relative_to(path))
        known.update(Path(name) for name in installed)
    actual = {
        Path(entry.path).relative_to(path): entry for entry in entries(path, set())
    }
    for relative in actual:
        if (
            relative.name != "RECORD"
            or not relative.parent.name.endswith(".dist-info")
            or not re.fullmatch(
                r"lib/python\d+\.\d+/site-packages", str(relative.parent.parent)
            )
        ):
            continue
        installer = relative.with_name("INSTALLER")
        if (
            installer not in actual
            or cache_metadata(path / installer).strip() != b"uv-pixi"
        ):
            continue
        records_txt = cache_metadata(path / relative).decode("utf-8").splitlines()
        for row in csv.reader(records_txt):
            if len(row) != 3 or Path(row[0]).is_absolute():
                return False
            installed_path = Path(
                os.path.abspath(path / relative.parent.parent / row[0])
            )
            if not installed_path.is_relative_to(path):
                return False
            known.add(installed_path.relative_to(path))
    for relative, entry in actual.items():
        if relative in known and (
            entry.is_file(follow_symlinks=False) or entry.is_symlink()
        ):
            continue
        if (
            entry.is_file(follow_symlinks=False)
            and relative.parent.name == "__pycache__"
        ):
            if match := re.fullmatch(
                r"(.+)\.cpython-\d+(?:\.opt-\d+|-pytest-\d+\.\d+\.\d+)?\.pyc",
                relative.name,
            ):
                if relative.parent.parent / (match[1] + ".py") in known:
                    continue
        return False
    return True


def verified_cache(path: Path, name: str) -> bool:
    directory = path / name
    if directory.resolve() != directory or not directory.is_dir():
        return False
    try:
        if name == ".pixi/envs":
            lock = cache_metadata(path / "pixi.lock")
            locked = set(
                re.findall(rb"(?m)^[ \t]*- conda: (https?://\S+)[ \t]*$", lock)
            )
            envs = list(directory.iterdir())
            return bool(envs) and all(
                env.is_dir()
                and not env.is_symlink()
                and pixi_environment(env, path, locked)
                for env in envs
            )
        if name != ".pixi/task-cache-v0" and not cache_tagged(directory):
            return False
        for entry in entries(directory, set()):
            if not entry.is_file(follow_symlinks=False):
                return False
            relative = Path(entry.path).relative_to(directory)
            text = str(relative)
            if name == ".pixi/task-cache-v0":
                if not re.fullmatch(r"[\w.-]+-[\da-f]{16}\.json", text, re.ASCII):
                    return False
                task = json.loads(cache_metadata(directory / relative))
                if not (
                    isinstance(task, dict)
                    and set(task) == {"hash"}
                    and isinstance(task["hash"], str)
                    and re.fullmatch(r"[\da-f]{16}", task["hash"])
                ):
                    return False
            elif text in {"CACHEDIR.TAG", ".gitignore"}:
                continue
            elif name == ".ruff_cache" and re.fullmatch(r"\d+\.\d+\.\d+/\d+", text):
                if not cache_metadata(directory / relative).startswith(
                    os.fsencode(path)
                ):
                    return False
            elif name == ".mypy_cache" and (
                re.fullmatch(r"\d+\.\d+/cache\.\d+\.db", text)
                or re.fullmatch(r"\d+\.\d+/[\w./-]+\.(meta|data)\.json", text, re.ASCII)
                or text == "missing_stubs"
            ):
                if relative.suffix == ".db":
                    fd = os.open(
                        entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    )
                    with os.fdopen(fd, "rb") as handle:
                        if handle.read(16) != b"SQLite format 3\0":
                            return False
                elif relative.suffix == ".json":
                    if not isinstance(
                        json.loads(cache_metadata(directory / relative)), dict
                    ):
                        return False
            elif name == ".pytest_cache" and text in {
                "README.md",
                "v/cache/nodeids",
                "v/cache/lastfailed",
                "v/cache/stepwise",
            }:
                value = cache_metadata(directory / relative)
                if text == "README.md":
                    if not value.startswith(b"# pytest cache directory #\n"):
                        return False
                elif not isinstance(
                    json.loads(value), dict if relative.name == "lastfailed" else list
                ):
                    return False
            else:
                return False
        return True
    except (FileNotFoundError, NotADirectoryError, ValueError, csv.Error):
        return False


@dataclass
class Inventory:
    blocked: str = ""
    retained: str = ""
    evidence: dict[Path, Stamp] = field(default_factory=dict)
    caches: dict[str, Stamp] = field(default_factory=dict)


def inventory(path: Path, tracked: set[Path]) -> Inventory:
    inventory = Inventory()
    size = 0
    excluded = set()
    for name in sorted(CACHE_DIRS):
        directory = path / name
        if not directory.is_dir() or directory.is_symlink():
            continue
        excluded.add(directory)
        tracked_cache = any(item.is_relative_to(name) for item in tracked)
        pixi_unlocked = name == ".pixi/envs" and Path("pixi.lock") not in tracked
        if not tracked_cache and not pixi_unlocked and verified_cache(path, name):
            inventory.caches[name] = stamp(directory.lstat())
        else:
            inventory.retained = inventory.retained or f"unknown-data: {name}"
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
            inventory.retained = inventory.retained or f"unknown-data: {relative}"
            continue
        info = entry.stat(follow_symlinks=False)
        if relative in CACHE_MARKERS and (
            info.st_size == len(CACHE_MARKERS[relative])
            and cache_metadata(path / relative) == CACHE_MARKERS[relative]
        ):
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
        allowed = (relative.parts[0] == "out" and relative.suffix in TEXT_SUFFIXES) or (
            relative.parts[0] == "scratch"
            and relative.suffix in SCRATCH_SOURCE_SUFFIXES
        )
        if not allowed or any(SENSITIVE_NAME.search(part) for part in relative.parts):
            inventory.retained = inventory.retained or f"unknown-data: {relative}"
            continue
        if info.st_size > MAX_FILE or size + info.st_size > MAX_ARCHIVE:
            inventory.retained = inventory.retained or f"evidence-too-large: {relative}"
            continue
        if info.st_uid != os.geteuid() or info.st_nlink != 1:
            inventory.retained = inventory.retained or f"unsafe-evidence: {relative}"
            continue
        expected = stamp(info)
        data = read_evidence(path, relative, expected)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            data = b"\0"
        if b"\0" in data:
            inventory.retained = inventory.retained or f"non-text-evidence: {relative}"
            continue
        size += info.st_size
        inventory.evidence[relative] = expected
    return inventory


def prune_cache(path: Path, name: str, expected: Stamp) -> None:
    if not shutil.rmtree.avoids_symlink_attacks:
        raise OSError("Safe cache removal is unavailable")
    relative = Path(name)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        parent = os.open(path, flags)
        stack.callback(os.close, parent)
        for part in relative.parts[:-1]:
            parent = os.open(part, flags, dir_fd=parent)
            stack.callback(os.close, parent)
        info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or stamp(info) != expected:
            raise OSError(f"Cache changed before removal: {name}")
        shutil.rmtree(Path("/proc/self/fd") / str(parent) / relative.name)
