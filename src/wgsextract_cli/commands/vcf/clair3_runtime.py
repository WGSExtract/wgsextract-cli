"""Clair3 invocation and preflight for the code-only streaming-MPS package."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import (
    MissingDependencyError,
    WGSExtractError,
    run_command,
)

MPS_CLAIR3 = "wgse-clair3"
CLAIR3 = "run_clair3.sh"
GPU_CLAIR3 = "/opt/bin/run_clair3.sh"


def clair3_command(
    reads: str,
    ref: str,
    model: Path,
    threads: str,
    output: str,
    bed: str | None,
    sample: str,
    gpu_devices: tuple[int, ...] = (),
    mps: bool = False,
) -> list[str]:
    command = [
        GPU_CLAIR3 if gpu_devices else MPS_CLAIR3 if mps else CLAIR3,
        f"--bam_fn={reads}",
        f"--ref_fn={ref}",
        f"--threads={threads}",
        "--platform=ont",
        f"--model_path={model}",
        f"--sample_name={sample}",
        f"--output={output}",
    ]
    if bed:
        command.append(f"--bed_fn={bed}")
    if gpu_devices:
        devices = ",".join(str(device) for device in gpu_devices)
        command += ["--use_gpu=True", f"--device=cuda:{devices}"]
    elif mps:
        # MPS must not select CUDA's disk-tensor pipeline or depth handling.
        command += ["--use_gpu=False", "--device=mps"]
    return command


def clair3_env() -> dict[str, str]:
    # Clair3 runs its own Python; don't leak ours into it.
    env = dict(os.environ)
    for var in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(var, None)
    return env


@dataclass(frozen=True)
class MpsSetup:
    """Local code-only build; MPS inference keeps CPU streaming and scaling."""

    version: str
    launcher: Path
    upstream_version: tuple[int, ...] = (2, 0, 5)


def verify_mps() -> MpsSetup:
    missing = (
        "Apple Silicon --gpu requires the WGSE-built wgse-clair3 package "
        "in the clair3 environment; see native/clair3/README.md. "
        "Nothing is installed automatically; run without --gpu for the CPU."
    )
    launcher = get_tool_path(MPS_CLAIR3)
    if not launcher:
        raise MissingDependencyError(missing)
    try:
        result = run_command(
            [MPS_CLAIR3, "--wgse-device-check"],
            env=clair3_env(),
            capture_output=True,
        )
        probe = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise MissingDependencyError(
            f"Couldn't check Clair3's MPS build. {missing}"
        ) from error
    if not isinstance(probe, dict) or (
        probe.get("package") != MPS_CLAIR3
        or probe.get("streaming_mps") != 1
        or probe.get("clair3_version") != "2.0.5"
    ):
        raise MissingDependencyError(missing)
    if probe.get("mps_available") is not True:
        raise WGSExtractError(
            "Clair3: PyTorch MPS is unavailable on this Mac "
            "(torch.backends.mps.is_available() is False). "
            "Run without --gpu for the CPU."
        )
    return MpsSetup(
        f"2.0.5 (wgse-clair3 {probe.get('version')}, MPS)",
        Path(launcher),
    )
