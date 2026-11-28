"""Optional tools ``deps install`` unpacks from an official release archive,
where no Pixi build exists for the platform: PLINK 2 on Windows, and Dorado.

Each release is pinned by URL and SHA-256, downloaded only by an explicit
``deps install``, and unpacked under ``managed_binary_dir(tool)``: a release
folder as ``release`` whatever its version, so a newer pin replaces it. A
reinstall or uninstall first moves the old copy aside, so a file in use leaves
it whole. Uninstall removes only what install unpacked: that folder may hold
the user's files.
A release under a restrictive licence (Dorado's is research-only) installs only
once the user accepts it (``deps install TOOL --accept-license``).
"""

from __future__ import annotations

import logging
import platform
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from wgsextract_cli.core import windows_arm64
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    UsageError,
    WGSExtractError,
)

ENVIRONMENT = "jartools"  # deps list's label for CLI-managed downloads


@dataclass(frozen=True)
class License:
    """A licence the user must accept before ``deps install`` fetches the tool."""

    name: str
    terms: str
    url: str


@dataclass(frozen=True)
class ManagedBinary:
    """An official release archive. ``unpack`` is the archive member install
    unpacks: a file, or a folder ending in ``/`` (installed as ``release``).
    ``executable`` is the tool's path under ``managed_binary_dir(tool)``.
    ``large`` asks for ``--allow-large-download`` under ``--network low``."""

    version: str
    url: str
    sha256: str
    size_label: str
    unpack: str
    executable: str
    license: License | None = None
    large: bool = True

    @property
    def installed(self) -> str:
        """What install leaves in ``managed_binary_dir(tool)``."""
        return "release" if self.unpack.endswith("/") else self.unpack


DORADO_LICENSE = License(
    name="Oxford Nanopore Technologies PLC. Public License Version 2.0",
    terms="use for research purposes only, with data from Oxford Nanopore devices",
    url="https://github.com/nanoporetech/dorado/blob/v2.2.0/LICENCE.txt",
)
_DORADO_CDN = "https://cdn.oxfordnanoportal.com/software/analysis/"


def _dorado(platform_name: str, archive: str, sha256: str, size: str) -> ManagedBinary:
    exe = "dorado.exe" if platform_name == "win64" else "dorado"
    return ManagedBinary(
        version="2.2.0",
        url=f"{_DORADO_CDN}dorado-2.2.0-{platform_name}.{archive}",
        sha256=sha256,
        size_label=size,
        unpack=f"dorado-2.2.0-{platform_name}/",
        executable=f"release/bin/{exe}",
        license=DORADO_LICENSE,
    )


# By tool, then by Pixi platform.
# - PLINK 2 has no Bioconda win-64 build; its author publishes versioned
#   Windows builds (GPL-3). The generic x86-64 build, not AVX2, runs on any
#   64-bit Intel or AMD CPU.
# - Dorado has no conda package; ONT publishes per-platform releases (their
#   Linux and Windows builds bundle CUDA). The Linux ARM64 build is ONT's
#   default one (CUDA 12), not the CUDA 13 build for Jetson Thor / DGX Spark.
MANAGED_BINARIES: dict[str, dict[str, ManagedBinary]] = {
    "plink2": {
        "win-64": ManagedBinary(
            version="2.0.0-b.1.4 (7 Nov 2026)",
            url="https://s3.amazonaws.com/plink2-assets/beta1/plink2_win64_20261107.zip",
            sha256="0a7c1bff1d5a3a7a01a27f789c4fbd92f7631c7ed27fd376b8c36fe2cf6f3f48",
            size_label="~11 MB",
            unpack="plink2.exe",
            executable="plink2.exe",
            large=False,
        ),
    },
    "dorado": {
        "linux-64": _dorado(
            "linux-x64",
            "tar.gz",
            "c2376701a48bc6227a9cb5ad1d0b9ada60b5f3c8760a657066131fdf0d9545c4",
            "~3.5 GB",
        ),
        "linux-aarch64": _dorado(
            "linux-arm64",
            "tar.gz",
            "5f2056d5d09562cc284b8340e0a0e13a317e2aa8476dc8f442f1a3136049dad5",
            "~1.8 GB",
        ),
        "osx-arm64": _dorado(
            "osx-arm64",
            "zip",
            "6e5e0291353c9d82044541301803da33119a1a3b46595fcca24f87d7cf79f651",
            "~52 MB",
        ),
        "win-64": _dorado(
            "win64",
            "zip",
            "7db872d3333e25f76613a634b3c30085851167ece8dc81ecffad0d40ddecebd5",
            "~2.8 GB",
        ),
    },
}


def host_platform() -> str | None:
    """This computer's Pixi platform name, where a managed release may apply."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win-64" if windows_arm64.windows_native_machine() == 0x8664 else None
    if sys.platform.startswith("linux"):
        return {"x86_64": "linux-64", "aarch64": "linux-aarch64"}.get(machine)
    if sys.platform == "darwin":
        return {"arm64": "osx-arm64", "x86_64": "osx-64"}.get(machine)
    return None


def managed_binary(tool: str) -> ManagedBinary | None:
    """The release ``deps install`` unpacks for ``tool`` on this computer."""
    host = host_platform()
    return MANAGED_BINARIES.get(tool, {}).get(host) if host else None


def license_required(tool: str) -> License | None:
    """The licence the user must accept before ``tool`` installs, if any."""
    spec = managed_binary(tool)
    return spec.license if spec else None


def license_fields(spec: ManagedBinary) -> dict[str, Any]:
    """What ``deps list`` and ``deps install`` report about a release's licence."""
    if spec.license is None:
        return {}
    return {
        "license": {
            "name": spec.license.name,
            "terms": spec.license.terms,
            "url": spec.license.url,
        }
    }


def describe(tool: str, spec: ManagedBinary) -> str:
    """The ``deps list`` note of an installable release."""
    note = f"Downloads the official {tool} {spec.version} release ({spec.size_label})."
    if spec.license:
        note += f" Licensed under the {spec.license.name}: {spec.license.terms}."
    return note


def managed_binary_dir(tool: str) -> Path:
    """Folder ``deps install`` unpacks ``tool``'s release into:
    ``<managed_jar_dir()>/<tool>``."""
    from wgsextract_cli.core.dependencies import managed_jar_dir

    return managed_jar_dir() / tool


def managed_executable(tool: str) -> str | None:
    """``tool``'s executable as ``deps install`` unpacked it, if it is there."""
    spec = managed_binary(tool)
    if spec is None:
        return None
    path = managed_binary_dir(tool) / spec.executable
    return str(path) if path.is_file() else None


def is_managed(tool: str) -> bool:
    """Whether ``deps install`` has unpacked ``tool``, so uninstall can remove
    it (whichever copy PATH finds first)."""
    return managed_executable(tool) is not None


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except (OSError, RuntimeError):
        return False


def _wanted(name: str, spec: ManagedBinary) -> bool:
    name = name.removeprefix("./")
    if spec.unpack.endswith("/"):
        return name.startswith(spec.unpack)
    return name == spec.unpack


def _link_inside(link: Path, target: str, into: Path) -> bool:
    """Whether the symlink ``link`` -> ``target`` points inside ``into``.
    ONT's Linux releases carry development links to their build machine
    (lib/libcudnn.so -> /etc/alternatives/...), which would dangle here:
    they are left out, never followed."""
    return not PurePosixPath(target).is_absolute() and _inside(
        link.parent / target, into
    )


def _unpack_zip(archive: Path, spec: ManagedBinary, into: Path) -> None:
    """Unpack ``spec.unpack`` from a zip, keeping Unix modes and, except on
    Windows (where links need privileges), the symlinks inside the release."""
    import zipfile

    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            if not _wanted(info.filename, spec):
                continue
            target = into / PurePosixPath(info.filename)
            if not _inside(target, into):
                raise WGSExtractError(
                    f"{archive.name} has an unsafe path: {info.filename}"
                )
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                link = bundle.read(info).decode()
                if sys.platform != "win32" and _link_inside(target, link, into):
                    target.symlink_to(link)
                else:
                    logging.debug(f"Skipped the link {info.filename} -> {link}.")
                continue
            with bundle.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            if mode & 0o777:
                target.chmod(mode & 0o777)


def _unpack_tar(archive: Path, spec: ManagedBinary, into: Path) -> None:
    """Unpack ``spec.unpack`` from a tarball in one pass (a gzip stream can't
    seek back), with tarfile's ``data`` filter and links kept only inside."""
    import tarfile

    if not hasattr(tarfile, "data_filter"):
        raise WGSExtractError(
            "Unpacking this release needs a Python with tarfile extraction "
            "filters (3.10.12, 3.11.4, 3.12 or newer)."
        )

    def keep(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
        link = Path(dest) / member.name
        if member.issym() and not _link_inside(link, member.linkname, into):
            logging.debug(f"Skipped the link {member.name} -> {member.linkname}.")
            return None
        return tarfile.data_filter(member, dest)

    with tarfile.open(archive) as bundle:
        wanted = (member for member in bundle if _wanted(member.name, spec))
        bundle.extractall(into, members=wanted, filter=keep)


def _drop_escaping_links(folder: Path) -> None:
    """Remove links that point outside ``folder`` once every link exists (one
    made before another it passes through could not be checked when made)."""
    for path in list(folder.rglob("*")):
        if path.is_symlink() and not _inside(path, folder):
            logging.debug(f"Removed the link {path}: it points outside the release.")
            path.unlink()


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _set_aside(path: Path) -> Path | None:
    """Rename ``path`` to ``.old-<name>`` beside it (on Windows this fails,
    untouched, while a file in it is in use); None when there is no ``path``."""
    if not (path.exists() or path.is_symlink()):
        return None
    aside = path.with_name(f".old-{path.name}")
    _remove(aside)
    path.replace(aside)
    return aside


def install(tool: str, spec: ManagedBinary, *, accept_license: bool) -> dict[str, Any]:
    """Download ``spec``'s archive, check its SHA-256, and unpack only
    ``spec.unpack`` into ``managed_binary_dir(tool)``."""
    import tarfile
    import zipfile

    from wgsextract_cli.core.dependencies import clear_tool_caches, get_tool_path
    from wgsextract_cli.core.ref_library.downloads import download_file

    if spec.license and not accept_license:
        raise UsageError(
            f"{tool} is licensed under the {spec.license.name}: "
            f"{spec.license.terms} (read it at {spec.license.url}). To accept "
            f"it and install, run: wgsextract deps install {tool} --accept-license"
        )
    dest = managed_binary_dir(tool)
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / spec.url.rsplit("/", 1)[-1]
    staging = dest / ".unpacking"
    logging.info(f"Downloading {tool} {spec.version} ({spec.size_label})...")
    ok = download_file(
        spec.url,
        str(archive),
        expected_sha256=spec.sha256,
        use_mirrors=False,
        resolve_release_digest=False,
        large=spec.large,
    )
    if not ok or not archive.is_file():
        raise WGSExtractError(f"Failed to download {tool} from {spec.url}.")
    target = dest / spec.installed
    try:
        _remove(staging)
        staging.mkdir()
        if archive.name.endswith(".zip"):
            _unpack_zip(archive, spec, staging)
        else:
            _unpack_tar(archive, spec, staging)
        unpacked = staging / spec.unpack.rstrip("/")
        if not (unpacked.exists() or unpacked.is_symlink()):
            raise KeyError(spec.unpack)
        if unpacked.is_dir():
            _drop_escaping_links(unpacked)
        aside = _set_aside(target)
        try:
            unpacked.replace(target)
        except OSError:
            if aside is not None:
                aside.replace(target)
            raise
        executable = dest / spec.executable
        if executable.is_file():
            executable.chmod(executable.stat().st_mode | 0o755)
        if aside is not None:
            _remove(aside)
    except (KeyError, zipfile.BadZipFile, tarfile.TarError) as error:
        raise WGSExtractError(
            f"The {tool} download from {spec.url} isn't the expected archive: {error}"
        ) from error
    except OSError as error:
        raise WGSExtractError(
            f"Couldn't unpack {tool} into {dest} (is it running?): {error}"
        ) from error
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            _remove(staging)

    clear_tool_caches()
    path = get_tool_path(tool)
    if not path:
        raise MissingDependencyError(
            f"Unpacked {tool} into {dest}, but it isn't found."
        )
    return {
        "tool": tool,
        "environment": ENVIRONMENT,
        "tools": [tool],
        "path": path,
        **license_fields(spec),
    }


def uninstall(tool: str, spec: ManagedBinary) -> dict[str, Any]:
    """Remove what ``install`` unpacked, then its folder if nothing else is in it."""
    from wgsextract_cli.core.dependencies import clear_tool_caches

    folder = managed_binary_dir(tool)
    if not is_managed(tool):
        raise WGSExtractError(f"No WGS Extract-managed {tool} is installed to remove.")
    logging.info(f"Removing {tool} from {folder}...")
    try:
        aside = _set_aside(folder / spec.installed)
        if aside is not None:
            _remove(aside)
        if not any(folder.iterdir()):
            folder.rmdir()
    except OSError as error:
        raise WGSExtractError(
            f"Couldn't remove {tool} from {folder} (is it running?): {error}"
        ) from error
    clear_tool_caches()
    return {
        "tool": tool,
        "environment": ENVIRONMENT,
        "tools": [tool],
        "path": str(folder),
    }
