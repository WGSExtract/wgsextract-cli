"""Installed components for ``deps ready``: each tool a command runs, with
its path, version, Conda package and the licence that package records, and
Docker images with the identity and labels the local engine reports.

Everything is read locally: tools are found and probed as ``deps check``
finds and probes them (a Pixi environment is never installed), and images
are inspected (``docker image inspect``), never pulled.
"""

from __future__ import annotations

import json
import shlex
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from wgsextract_cli.core import optional_tools, runtime
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.dependency_checks import (
    MINIMUM_VERSIONS,
    check_all_dependencies,
    too_old,
)
from wgsextract_cli.core.pixi_tools import is_pixi_tool_command, pixi_env_prefix
from wgsextract_cli.core.tool_catalog import MANDATORY_TOOLS

from .model import (
    INCOMPATIBLE,
    INSTALLED,
    MISSING,
    RUNNABLE,
    UNSUPPORTED,
    Item,
    terms,
)

# The OCI label an image states its licence in.
LICENCE_LABEL = "org.opencontainers.image.licenses"


def tool_item(tool: str, *, required: bool = True, override: str | None = None) -> Item:
    """``tool`` as the command would find it: ``override`` when the user
    named its executable, else where ``deps check`` finds it."""
    if override:
        named = Path(override).expanduser()
        resolved = str(named) if named.is_file() else shutil.which(override)
        return Item(
            "tool",
            tool,
            INSTALLED if resolved else MISSING,
            required,
            detail="named in its option or config.toml setting",
            path=resolved or str(named),
            fix_text=None if resolved else f"{override} doesn't exist.",
        )
    group = "mandatory" if tool in MANDATORY_TOOLS else "optional"
    row = check_all_dependencies([], [tool])["optional"][0]
    info = optional_tools.tool_row(row, group)
    version = row.get("version")
    if info["status"] == optional_tools.UNAVAILABLE:
        return Item("tool", tool, UNSUPPORTED, required, detail=info["note"] or "")
    if info["status"] == optional_tools.MISSING:
        found = get_tool_path(tool)
        error = (
            version if isinstance(version, str) and version.startswith("Error:") else ""
        )
        status = INCOMPATIBLE if found and "not installed" not in error else MISSING
        return Item(
            "tool",
            tool,
            status,
            required,
            detail=error.removeprefix("Error:").strip() or "not found",
            path=found,
            fix_arguments=["deps", "install", tool] if info["can_install"] else None,
            fix_text=None if info["can_install"] else info["note"] or _hint(version),
        )
    path = str(row["path"])
    if old := too_old(tool, version):
        minimum = ".".join(map(str, MINIMUM_VERSIONS[tool]))
        return Item(
            "tool",
            tool,
            INCOMPATIBLE,
            required,
            detail=f"{old} {minimum} or newer is needed.",
            path=path,
            version=version,
            evidence=package_evidence(path, tool),
        )
    return Item(
        "tool",
        tool,
        RUNNABLE if version else INSTALLED,
        required,
        detail=f"from {row['alt_env'] or row['runtime']}",
        path=path,
        version=version,
        evidence=package_evidence(path, tool),
    )


def _hint(version: object) -> str | None:
    """``deps check``'s install hint, which it reports as a missing tool's
    version."""
    return version if isinstance(version, str) and version else None


def package_evidence(path: str, tool: str) -> dict[str, Any]:
    """The Conda package an installed ``tool`` (found at ``path``) came
    from, and the licence that package's own metadata records."""
    record = _package_record(path, tool)
    if record is None:
        return {"package": None, "terms": terms()}
    package = {
        key: record.get(key)
        for key in ("name", "version", "build", "channel", "sha256")
    }
    licence = record.get("license")
    source = f"conda package metadata ({record['name']})"
    return {
        "package": package,
        "terms": terms(str(licence) if licence else None, source),
    }


def _tool_file(path: str, tool: str) -> Path | None:
    """The file a resolved tool runs: ``path``, or for a ``pixi run -e ENV``
    launcher the tool's file in that environment."""
    if not is_pixi_tool_command(path):
        return Path(path)
    try:
        argv = shlex.split(path, posix=not runtime.is_windows_host())
    except ValueError:
        return None
    env = next(
        (b for a, b in zip(argv, argv[1:], strict=False) if a == "-e"), "default"
    )
    prefix = pixi_env_prefix(env)
    if prefix is None:
        return None
    for folder in ("bin", "Scripts", "Library/bin"):
        candidate = prefix / folder / Path(argv[-1]).name
        if candidate.exists():
            return candidate
    return prefix / "bin" / Path(argv[-1] or tool).name


def _package_record(path: str, tool: str) -> dict[str, Any] | None:
    file = _tool_file(path, tool)
    if file is None:
        return None
    for candidate in dict.fromkeys((file, file.resolve())):
        prefix = next(
            (p for p in candidate.parents if (p / "conda-meta").is_dir()), None
        )
        if prefix is None:
            continue
        relative = candidate.relative_to(prefix).as_posix()
        record = _owners(str(prefix)).get(relative)
        if record:
            return record
    return None


@lru_cache(maxsize=16)
def _owners(prefix: str) -> dict[str, dict[str, Any]]:
    """Each file of the Conda environment at ``prefix``, by its relative
    path, mapped to the package record that installed it."""
    owners: dict[str, dict[str, Any]] = {}
    for meta in sorted(Path(prefix, "conda-meta").glob("*.json")):
        try:
            record = json.loads(meta.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or "name" not in record:
            continue
        summary = {
            key: record.get(key)
            for key in ("name", "version", "build", "channel", "sha256", "license")
        }
        for name in record.get("files") or ():
            owners.setdefault(str(name).replace("\\", "/"), summary)
    return owners


def image_evidence(image: str, found: dict[str, Any] | None) -> dict[str, Any]:
    """A local image's identity (``image_id``, the ``repo_digests`` it was
    pulled by, its ``platform``) and the licence its labels state, from
    ``docker image inspect`` output ``found``."""
    if not found:
        return {"image": image, "image_id": None, "terms": terms()}
    labels = (found.get("Config") or {}).get("Labels") or {}
    licence = labels.get(LICENCE_LABEL) if isinstance(labels, dict) else None
    system, machine = found.get("Os"), found.get("Architecture")
    return {
        "image": image,
        "image_id": found.get("Id"),
        "repo_digests": list(found.get("RepoDigests") or []),
        "platform": f"{system}/{machine}" if system and machine else None,
        "terms": terms(licence, f"image label {LICENCE_LABEL}"),
    }


def image_item(image: str, *, backend_ok: bool, fix_text: str) -> Item:
    """``image`` as the local Docker engine has it: runnable once its
    backend's own checks passed. ``fix_text`` says how to get it (an
    explicit pull or a local build); nothing here pulls it."""
    from wgsextract_cli.core.containers import docker_info, inspect_image
    from wgsextract_cli.core.utils import WGSExtractError

    found = inspect_image(image)
    evidence = image_evidence(image, found)
    if found:
        return Item(
            "image",
            image,
            RUNNABLE if backend_ok else INSTALLED,
            detail="in the local Docker engine",
            evidence=evidence,
        )
    try:
        docker_info("")
    except WGSExtractError:
        return Item(
            "image",
            image,
            MISSING,
            required=False,
            detail="the Docker engine didn't answer, so its images can't be listed",
            evidence=evidence,
        )
    return Item(
        "image",
        image,
        MISSING,
        detail="not in the local Docker engine",
        evidence=evidence,
        fix_text=fix_text,
    )
