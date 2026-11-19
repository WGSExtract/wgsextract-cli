"""Explicit opt-in loading of the pinned, resource-only Linux CUDA sidecar."""

import json
import platform
import sys
import zipfile
from pathlib import Path

from wgsextract_cli.core.utils import UsageError


def cuda_pairhmm_java_options(sidecar: Path, gatk_version: str) -> list[str]:
    """Validate a local sidecar before asking GATK to execute native code."""
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
    try:
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read("META-INF/wgse-pairhmm.json"))
            resources = {
                f"com/intel/gkl/native/libgkl_pairhmm{suffix}.so"
                for suffix in ("", "_omp")
            }
            if not resources.issubset(archive.namelist()):
                raise ValueError("missing native PairHMM resources")
            if metadata != {
                "schema": 1,
                "gatk": "4.6.2.0",
                "gkl": "0.8.11",
                "backend": "cuda",
            }:
                raise ValueError("unsupported PairHMM sidecar metadata")
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise UsageError(
            f"Cannot use CUDA PairHMM sidecar {path}: {exc}. "
            "Build the local sidecar as described in native/pairhmm/README.md; "
            "nothing is installed or downloaded automatically."
        ) from exc
    if gatk_version != "4.6.2.0":
        raise UsageError(
            f"CUDA PairHMM sidecar requires GATK 4.6.2.0; found "
            f"{gatk_version or 'an unknown version'}."
        )
    return ["--java-options", f"-Xbootclasspath/a:{path}"]
