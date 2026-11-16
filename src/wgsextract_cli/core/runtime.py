import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_path

from wgsextract_cli.core.paths import windows_local_app_dir

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass(frozen=True)
class ThreadTuningProfile:
    threads: int
    label: str
    reason: str
    cpu_alignment_threads: int | None = None


@dataclass(frozen=True)
class CoreLayout:
    big: int
    little: int

    @property
    def total(self) -> int:
        return self.big + self.little


def is_windows_host() -> bool:
    return sys.platform == "win32"


def _sysctl_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _core_count(value: str) -> int | None:
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def macos_core_layout() -> CoreLayout | None:
    """Read core levels, not chip names; Super and Performance are both big."""
    if platform.system() != "Darwin" or platform.machine() not in {"arm64", "aarch64"}:
        return None
    output = _sysctl_output(["sysctl", "-a"]) or ""

    perf_levels: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            key, separator, value = line.partition("=")
        if not separator:
            continue

        key_parts = key.strip().split(".")
        if len(key_parts) != 3 or key_parts[0] != "hw":
            continue
        level_key, field = key_parts[1], key_parts[2]
        if not re.fullmatch(r"perflevel\d+", level_key) or field not in {
            "name",
            "physicalcpu",
        }:
            continue
        perf_levels.setdefault(level_key, {})[field] = value.strip().strip('"')

    levels = sorted(perf_levels, key=lambda level: int(level.removeprefix("perflevel")))
    counts = [
        _core_count(perf_levels[level].get("physicalcpu", "")) for level in levels
    ]
    if counts and all(count is not None for count in counts):
        named = all(perf_levels[level].get("name", "").strip() for level in levels)
        big = little = 0
        for index, (level, count) in enumerate(zip(levels, counts, strict=True)):
            assert count is not None
            efficiency = (
                perf_levels[level]["name"].strip().lower() == "efficiency"
                if named
                else len(levels) > 1 and index == len(levels) - 1
            )
            if efficiency:
                little += count
            else:
                big += count
        if big:
            return CoreLayout(big, little)

    # Older macOS releases may expose only the fastest level.
    fastest = _sysctl_output(["sysctl", "-n", "hw.perflevel0.physicalcpu"])
    count = _core_count(fastest) if fastest is not None else None
    return (
        CoreLayout(count, max(0, (os.cpu_count() or count) - count)) if count else None
    )


def macos_performance_core_count() -> int | None:
    layout = macos_core_layout()
    return layout.big if layout else None


def default_thread_tuning_profile() -> ThreadTuningProfile:
    """Return the central default thread policy for user-facing commands."""
    layout = macos_core_layout()
    if layout:
        return ThreadTuningProfile(
            threads=layout.big,
            label=str(layout.big),
            reason="Apple Silicon performance-core count",
            cpu_alignment_threads=layout.total,
        )

    total_cpus = os.cpu_count() or 4
    return ThreadTuningProfile(
        threads=max(1, total_cpus),
        label=str(max(1, total_cpus)),
        reason="all available cores",
    )


PROJECT_NAME = "wgsextract-cli"


@lru_cache(maxsize=1)
def source_checkout_root() -> Path | None:
    """Return the wgsextract-cli source tree this package runs from, if any.

    Only the nearest enclosing pyproject.toml is considered, and only when it
    declares this project, so an installed copy that happens to live inside
    some other repository never adopts that repository's directories.
    """
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            with pyproject.open("rb") as f:
                name = tomllib.load(f).get("project", {}).get("name")
        except (OSError, tomllib.TOMLDecodeError):
            return None
        return parent if name == PROJECT_NAME else None
    return None


def app_data_root() -> Path:
    """Per-user data directory used when not running from a source checkout."""
    return windows_local_app_dir(PROJECT_NAME, "WGSExtract") or user_data_path(
        PROJECT_NAME, "WGSExtract"
    )


def repo_root() -> Path:
    """Base for default data folders (reference/, datasets/, jartools/): the
    source checkout when running from one, else the per-user data directory."""
    return source_checkout_root() or app_data_root()
