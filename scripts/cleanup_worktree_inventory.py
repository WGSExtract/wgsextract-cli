"""Inspect and prune only named, recognizably generated worktree caches."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import json
import marshal
import os
import re
import shutil
import stat
import sys
import warnings
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


def cache_metadata(path: Path, limit: int = MAX_ARCHIVE) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Unexpected cache metadata")
        data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Unexpected cache metadata")
        return data


def cache_tagged(path: Path) -> bool:
    return cache_metadata(path / "CACHEDIR.TAG").partition(b"\n")[0] == CACHE_TAG


InstalledHash = tuple[str, str | None, bool]


def conda_hashes(package: dict[str, object]) -> dict[Path, InstalledHash]:
    paths_data = package.get("paths_data")
    if not isinstance(paths_data, dict) or not isinstance(
        paths := paths_data.get("paths"), list
    ):
        return {}
    result = {}
    for record in paths:
        if not isinstance(record, dict) or not isinstance(
            name := record.get("_path"), str
        ):
            raise ValueError("Malformed installed Conda path record")
        digest = record.get("sha256_in_prefix")
        if digest is None and not (
            record.get("prefix_placeholder") or record.get("file_mode")
        ):
            digest = record.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
            original = record.get("sha256")
            original_hash = (
                original
                if isinstance(original, str) and re.fullmatch(r"[a-f0-9]{64}", original)
                else None
            )
            result[Path(name)] = (
                digest,
                original_hash,
                record.get("path_type") == "softlink",
            )
    return result


def verify_installed(
    path: Path,
    entry: os.DirEntry[str],
    expected: InstalledHash,
    root: Path,
    hashes: dict[Path, InstalledHash | None],
    memo: dict[Stamp, str],
) -> bool:
    before = stamp(entry.stat(follow_symlinks=False))
    primary, original_target, is_conda_softlink = expected
    if entry.is_symlink():
        if not is_conda_softlink:
            return False
        link_digest = hashlib.sha256(os.fsencode(path.readlink())).hexdigest()
        matches = link_digest == primary
        if not matches and original_target:
            # Some Conda softlink records describe the target's bytes, not
            # the installed link spelling. The target is also checked below.
            target = path.resolve(strict=True)
            if target.is_relative_to(root):
                target_hash = hashes.get(target.relative_to(root))
                matches = (
                    target_hash is not None
                    and original_target in target_hash[:2]
                    and target.is_file()
                )
        return matches and stamp(path.lstat()) == before
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or stamp(info) != before:
            return False
        observed = memo.get(before)
        if observed is None:
            digest = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            observed = digest.hexdigest()
        if stamp(os.fstat(handle.fileno())) != before or stamp(path.lstat()) != before:
            return False
    if info.st_nlink > 1:
        memo[before] = observed
    return observed == primary


def pixi_environment(
    path: Path, tree: Path, locked: set[bytes], memo: dict[Stamp, str]
) -> bool:
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
    generated = {
        Path("CACHEDIR.TAG"),
        Path("conda-meta/pixi"),
        Path("conda-meta/history"),
        Path("conda-meta/pixi_env_prefix"),
        Path("conda-meta/.pixi-environment-fingerprint"),
    }
    records = list(metadata.glob("*.json"))
    if not records:
        return False
    installed_hashes: dict[Path, InstalledHash | None] = {}

    def remember(relative: Path, digest: InstalledHash | None) -> None:
        if relative in installed_hashes and installed_hashes[relative] != digest:
            raise ValueError("Conflicting installed file metadata")
        installed_hashes[relative] = digest

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
        generated.add(record.relative_to(path))
        hashes = conda_hashes(package)
        for name in installed:
            relative = Path(name)
            remember(relative, hashes.get(relative))
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
            logical = installed_path.relative_to(path)
            if logical == relative and not row[1] and not row[2]:
                generated.add(logical)
                continue
            if not re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", row[1]):
                return False
            digest = base64.urlsafe_b64decode(row[1][7:] + "=").hex()
            remember(logical, (digest, None, False))
    for relative, entry in actual.items():
        if relative in installed_hashes:
            expected = installed_hashes[relative]
            if expected is None or not verify_installed(
                path / relative, entry, expected, path, installed_hashes, memo
            ):
                return False
            continue
        if relative in generated and entry.is_file(follow_symlinks=False):
            continue
        # The source must also pass the installed-file hash check above.
        if (
            entry.is_file(follow_symlinks=False)
            and (source := bytecode_source(relative)) is not None
            and source in actual
            and installed_hashes.get(source)
            and matches_bytecode(path / relative, path / source)
        ):
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
            memo: dict[Stamp, str] = {}
            return bool(envs) and all(
                env.is_dir()
                and not env.is_symlink()
                and pixi_environment(env, path, locked, memo)
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


def bytecode_source(relative: Path) -> Path | None:
    if relative.parent.name != "__pycache__":
        return None
    tag = re.escape(sys.implementation.cache_tag or "")
    match = re.fullmatch(
        rf"(.+)\.{tag}(?:\.opt-[12]|-pytest-\d+\.\d+\.\d+)?\.pyc", relative.name
    )
    return relative.parent.parent / (match[1] + ".py") if match else None


def matches_bytecode(path: Path, source: Path) -> bool:
    if re.search(r"-pytest-\d+\.\d+\.\d+\.pyc$", path.name):
        return False
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return False
        header = handle.read(16)
        data = handle.read(MAX_FILE + 1)
    info = source.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or len(header) != 16
        or header[:4] != importlib.util.MAGIC_NUMBER
        or info.st_size > MAX_FILE
        or len(data) > MAX_FILE
    ):
        return False
    flags = int.from_bytes(header[4:8], "little")
    if flags not in {1, 3} and not (
        flags == 0
        and int.from_bytes(header[8:12], "little") == int(info.st_mtime) & 0xFFFFFFFF
        and int.from_bytes(header[12:16], "little") == info.st_size & 0xFFFFFFFF
    ):
        return False
    source_bytes = cache_metadata(source, MAX_FILE)
    if flags in {1, 3} and header[8:] != importlib.util.source_hash(source_bytes):
        return False
    optimized = re.search(r"\.opt-([12])\.pyc$", path.name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            expected = compile(
                source_bytes,
                str(source),
                "exec",
                dont_inherit=True,
                optimize=int(optimized[1]) if optimized else 0,
            )
    except SyntaxError:
        return False
    # Do not unmarshal untrusted bytes. Retain variants we cannot reproduce.
    return data == marshal.dumps(expected)


def inventory(path: Path, tracked: set[Path]) -> Inventory:
    inventory = Inventory()
    bytecode: dict[Path, Path] = {}
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
            inventory.retained = inventory.retained or f"unverified-cache: {name}"
    for entry in entries(path, excluded | {path / ".git"}):
        relative = Path(entry.path).relative_to(path)
        if relative in tracked:
            continue
        if entry.is_file(follow_symlinks=False) and (
            source := bytecode_source(relative)
        ):
            bytecode[relative] = source
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
    for relative, source in bytecode.items():
        try:
            if (source in tracked or source in inventory.evidence) and matches_bytecode(
                path / relative, path / source
            ):
                continue
        except (FileNotFoundError, ValueError):
            pass
        inventory.retained = inventory.retained or f"unknown-data: {relative}"
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
