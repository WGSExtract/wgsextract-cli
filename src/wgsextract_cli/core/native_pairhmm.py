"""Local opt-in CUDA and NEON GKL sidecars."""

import json
import os
import platform
import sys
import zipfile
from pathlib import Path
from typing import Any

from wgsextract_cli.core.utils import UsageError


def validate_pairhmm_sidecar(
    sidecar: Path, gatk_version: str, *, backend: str, resources: set[str]
) -> tuple[Path, dict[str, Any]]:
    """Validate pinned local inputs without installing or fetching anything."""
    path = sidecar.expanduser().resolve()
    if os.pathsep in str(path):
        raise UsageError(
            "PairHMM sidecar paths must not contain a classpath separator."
        )
    try:
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read("META-INF/wgse-pairhmm.json"))
            if not isinstance(metadata, dict) or any(
                metadata.get(key) != value
                for key, value in {
                    "schema": 1,
                    "gatk": "4.6.2.0",
                    "gkl": "0.8.11",
                    "backend": backend,
                }.items()
            ):
                raise ValueError("unsupported PairHMM sidecar metadata")
            if not resources.issubset(archive.namelist()):
                raise ValueError("missing native PairHMM resources")
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        recipe = (
            "native/pairhmm/README.md"
            if backend == "cuda"
            else "packaging/arm64/gkl/README.md"
        )
        raise UsageError(
            f"Cannot use {backend} PairHMM sidecar {path}: {exc}. "
            f"Build the local sidecar as described in {recipe}; "
            "nothing is installed or downloaded automatically."
        ) from exc
    if gatk_version != "4.6.2.0":
        raise UsageError(
            f"PairHMM sidecar requires GATK 4.6.2.0; found "
            f"{gatk_version or 'an unknown version'}."
        )
    return path, metadata


def cuda_pairhmm_java_options(sidecar: Path, gatk_version: str) -> list[str]:
    """Prepend the Linux CUDA native resources without replacing GKL classes."""
    if sys.platform != "linux" or platform.machine().lower() not in (
        "x86_64",
        "amd64",
    ):
        raise UsageError("--pair-hmm-cuda requires Linux x86-64.")
    path = sidecar.expanduser().resolve()
    # GATK's launcher whitespace-splits --java-options; ':' separates JVM paths.
    if any(c.isspace() for c in str(path)) or ":" in str(path):
        raise UsageError(
            "--pair-hmm-cuda needs a sidecar path without whitespace or ':'."
        )
    path, _ = validate_pairhmm_sidecar(
        path,
        gatk_version,
        backend="cuda",
        resources={
            f"com/intel/gkl/native/libgkl_pairhmm{suffix}.so" for suffix in ("", "_omp")
        },
    )
    return ["--java-options", f"-Xbootclasspath/a:{path}"]


def neon_pairhmm_command(sidecar: Path, gatk_version: str) -> list[str]:
    """Use the existing ARM loader overlay before GATK's bundled GKL classes."""
    if sys.platform != "darwin" or platform.machine().lower() not in (
        "arm64",
        "aarch64",
    ):
        raise UsageError("--pair-hmm-neon requires macOS Apple Silicon.")
    prefix = "com/intel/gkl/"
    path, metadata = validate_pairhmm_sidecar(
        sidecar,
        gatk_version,
        backend="neon",
        resources={
            f"{prefix}native/darwin-aarch64/libgkl_{name}.dylib"
            for name in ("utils", "pairhmm", "pairhmm_omp")
        }
        | {
            f"{prefix}{name}.class"
            for name in (
                "NativeLibraryLoader",
                "IntelGKLUtils",
                "pairhmm/IntelPairHmm",
                "pairhmm/IntelPairHmmOMP",
            )
        },
    )
    try:
        java = Path(metadata["java"])
        gatk = Path(metadata["gatk_jar"])
        if not java.is_absolute() or not os.access(java, os.X_OK):
            raise ValueError("the build-time JDK is missing or not executable")
        if not gatk.is_absolute() or os.pathsep in str(gatk):
            raise ValueError("invalid build-time GATK jar path")
        with zipfile.ZipFile(gatk) as archive:
            manifest = archive.read("META-INF/MANIFEST.MF").decode()
        if "Implementation-Version: 4.6.2.0" not in manifest.splitlines():
            raise ValueError("the build-time GATK jar is no longer version 4.6.2.0")
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError) as exc:
        raise UsageError(
            f"Cannot use NEON PairHMM build inputs: {exc}. Rebuild the sidecar "
            "against an installed JDK 17+ and GATK 4.6.2.0; nothing is downloaded."
        ) from exc
    return [
        str(java),
        "-Dsamjdk.use_async_io_read_samtools=false",
        "-Dsamjdk.use_async_io_write_samtools=true",
        "-Dsamjdk.use_async_io_write_tribble=false",
        "-Dsamjdk.compression_level=2",
        "-cp",
        os.pathsep.join((str(path), str(gatk))),
        "org.broadinstitute.hellbender.Main",
    ]
