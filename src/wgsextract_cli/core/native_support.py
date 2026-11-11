"""Architecture limits for tools without a qualified native ARM64 runtime."""

import platform
import shutil
import subprocess
import sys

from wgsextract_cli.core.utils import WGSExtractError


def is_linux_arm64() -> bool:
    return sys.platform.startswith("linux") and platform.machine().lower() in {
        "aarch64",
        "arm64",
    }


def is_arm64_docker_host() -> bool:
    """Linux ARM64, or an Apple Silicon Mac: Docker Desktop's Linux VM there
    is aarch64 too, so x86-64 images run only under emulation."""
    return is_linux_arm64() or (sys.platform == "darwin" and _apple_silicon())


def _apple_silicon() -> bool:
    """The Mac's CPU, not this process's: x86-64 Python under Rosetta reports
    x86_64 on Apple Silicon too."""
    if platform.machine().lower() in {"arm64", "aarch64"}:
        return True
    sysctl = shutil.which("sysctl") or "/usr/sbin/sysctl"
    try:
        found = subprocess.run(
            [sysctl, "-n", "hw.optional.arm64"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return found.returncode == 0 and found.stdout.strip() == "1"


def tool_unsupported_reason(tool: str) -> str | None:
    if not is_linux_arm64():
        return None
    if tool == "gatk":
        return (
            "GATK is not supported on Linux ARM64 in phase 1: the pinned GKL "
            "native PairHMM/Smith-Waterman kernels are x86-only. A slow "
            "pure-Java fallback is not enabled. Use bcftools calling instead; "
            "native GKL support is tracked in #236 phase 2."
        )
    if tool == "parabricks":
        return (
            "Parabricks --gpu is not supported on Linux ARM64 in phase 1: "
            "no qualified native ARM64 image is available. x86 Docker images "
            "and emulation are not supported. Use the native CPU pipeline instead."
        )
    return None


def require_native_tool(tool: str) -> None:
    if reason := tool_unsupported_reason(tool):
        raise WGSExtractError(reason)
