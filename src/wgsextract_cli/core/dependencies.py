import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core import runtime
from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.pixi_tools import (
    _host_pixi_tool_command,
    _join_command,
    is_pixi_tool_command,
    is_pixi_tool_path,
    locked_packages,
    pixi_env_prefix,
    pixi_env_tool,
)
from wgsextract_cli.core.tool_catalog import (
    ENV_ONLY_TOOLS,
    MANDATORY_TOOLS,
    OPTIONAL_TOOLS,
    PIXI_TOOL_ENVS,
    _tool_executable_names,
    optional_dependency_tools,
    required_dependency_tools,
)
from wgsextract_cli.core.tool_commands import _tool_command_parts

__all__ = [
    "MANDATORY_TOOLS",
    "OPTIONAL_TOOLS",
    "PIXI_TOOL_ENVS",
    "is_pixi_tool_command",
    "is_pixi_tool_path",
    "optional_dependency_tools",
    "pixi_env_prefix",
    "pixi_env_tool",
    "required_dependency_tools",
]


def get_tool_runtime(path: str | None) -> str:
    """Return where a resolved dependency comes from: pixi, native or missing."""
    if path is None:
        return "missing"
    if is_pixi_tool_path(path) or is_pixi_tool_command(path):
        return "pixi"
    return "native"


def get_jar_dir() -> str:
    """Returns the directory where JAR tools are expected to live."""
    from wgsextract_cli.core.config import settings

    configured = settings.get("jar_directory")
    if configured and isinstance(configured, str):
        expanded_path = expand_path(configured)
        if Path(expanded_path).is_dir():
            return expanded_path

    return str(runtime.repo_root() / "jartools")


def _which_tool(tool: str) -> str | None:
    for executable_name in _tool_executable_names(tool):
        path = shutil.which(executable_name)
        if path:
            return path
    return None


def get_tool_path(tool: str) -> str | None:
    """Return how to invoke a tool (path or wrapper command), or None if missing.

    A tool on PATH (inside `pixi run`, the default environment's bin, or
    Library\\bin on Windows) comes first, then the tool's own Pixi environment.
    Results are cached per process (resolution may spawn ``pixi run`` probes);
    ``clear_tool_caches`` drops them, and config reloads call it.
    """
    return _resolve_tool_path(tool)


# Successful version probes, by the command probed (see get_tool_version).
tool_versions: dict[tuple[str, ...], str] = {}


def clear_tool_caches() -> None:
    _resolve_tool_path.cache_clear()
    tool_versions.clear()
    locked_packages.clear()
    if "wgsextract_cli.core.mimalloc" in sys.modules:
        from wgsextract_cli.core.mimalloc import clear_allocator_caches

        clear_allocator_caches()
    if "wgsextract_cli.core.remote_input" in sys.modules:
        from wgsextract_cli.core.remote_input import clear_remote_input_cache

        clear_remote_input_cache()


def managed_jar_dir() -> Path:
    """Writable directory where CLI-installed optional JAR tools are stored."""
    from wgsextract_cli.core.config import settings

    configured = settings.get("jar_directory")
    if isinstance(configured, str) and configured.strip():
        return Path(expand_path(configured.strip()))
    reflib = settings.get("reference_library")
    if isinstance(reflib, str) and reflib.strip():
        return Path(expand_path(reflib.strip()), "jartools")
    return runtime.app_data_root() / "jartools"


def find_discvrseq_jar(explicit: str | None = None) -> str | None:
    """Locate DISCVRseq.jar from explicit path, config discvrseq_jar, or jartools folders."""
    from wgsextract_cli.core.config import settings

    if explicit and explicit.strip():
        resolved = Path(expand_path(explicit.strip()))
        return str(resolved) if resolved.is_file() else None
    configured = settings.get("discvrseq_jar")
    if isinstance(configured, str) and configured.strip():
        resolved = Path(expand_path(configured.strip()))
        if resolved.is_file():
            return str(resolved)
    search_dirs: list[Path] = []
    for folder in (
        managed_jar_dir(),
        Path(get_jar_dir()),
        runtime.app_data_root() / "jartools",
    ):
        if folder not in search_dirs:
            search_dirs.append(folder)
    for folder in search_dirs:
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if (
                entry.is_file()
                and entry.name.lower().startswith("discvrseq")
                and entry.name.lower().endswith(".jar")
            ):
                return str(entry)
    return None


_JAVA17_RE = re.compile(r'(?:version\s+"|openjdk\s+)17(?:\.|\b)', re.IGNORECASE)


def _is_java17(java_path: str) -> bool:
    try:
        res = subprocess.run(
            [java_path, "-version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    combined = f"{res.stdout}\n{res.stderr}"
    return bool(_JAVA17_RE.search(combined))


@lru_cache(maxsize=256)
def _resolve_tool_path(tool: str) -> str | None:
    if tool == "java17":
        from wgsextract_cli.core.config import settings

        configured = settings.get("java17_executable")
        if isinstance(configured, str) and configured.strip():
            resolved = Path(expand_path(configured.strip()))
            if resolved.is_file():
                return str(resolved)
        pixi_java17 = _host_pixi_tool_command("java17")
        if pixi_java17:
            return pixi_java17
        on_path = _which_tool("java")
        if on_path and _is_java17(on_path):
            return on_path
        return None
    if tool in ENV_ONLY_TOOLS:
        return _host_pixi_tool_command(tool)
    if tool == "discvrseq":
        on_path = _which_tool("discvrseq")
        if on_path:
            return on_path
        jar = find_discvrseq_jar()
        java = (
            (_resolve_tool_path("java17") or _resolve_tool_path("java"))
            if jar
            else None
        )
        if jar and java:
            return _join_command([*_tool_command_parts(java), "-jar", jar])
        return None
    if tool == "granite":
        from wgsextract_cli.core.config import settings

        configured = settings.get("granite_executable")
        if isinstance(configured, str) and configured.strip():
            resolved = Path(expand_path(configured.strip()))
            if resolved.is_file():
                return str(resolved)
    if tool == "multiqc":
        return _host_pixi_tool_command("multiqc") or _which_tool("multiqc")
    return _which_tool(tool) or _host_pixi_tool_command(tool)


def check_dependencies(tool_list: list[str], jar_dir: str | None = None) -> list[str]:
    """
    Checks if all required tools or JAR files are available in the PATH or Pixi.
    Returns a list of missing tools.
    """
    missing = []
    if jar_dir is None:
        jar_dir = get_jar_dir()

    for tool in tool_list:
        if tool.endswith(".jar"):
            if not Path(jar_dir, tool).exists():
                missing.append(f"{tool} (in {jar_dir})")
        else:
            if get_tool_path(tool) is None:
                missing.append(tool)
    return missing
