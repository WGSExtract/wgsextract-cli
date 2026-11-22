"""``deps ready -- vcf clair3 ...``: whether this host can call with Clair3,
on the backend the options choose (the CPU, Apple Silicon MPS, or HKU's
CUDA image), and with which model, checked by the very functions a run
checks with (``require_supported_platform``, ``verify_gpu``,
``choose_model``, ``model_evidence``). Nothing is pulled or downloaded."""

from __future__ import annotations

import sys
from pathlib import Path

from wgsextract_cli.api.vcf import VcfClair3Params
from wgsextract_cli.core.alignment.metadata import get_bam_header
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.parabricks import gpu_driver_version, gpu_memory
from wgsextract_cli.core.readiness.components import (
    image_item,
    package_evidence,
    tool_item,
)
from wgsextract_cli.core.readiness.model import (
    INCOMPATIBLE,
    INSTALLED,
    MISSING,
    RUNNABLE,
    UNSUPPORTED,
    UNVERIFIED,
    Item,
    status_of,
)
from wgsextract_cli.core.utils import WGSExtractError

from .clair3 import (
    GpuSetup,
    clair3_gpu_image,
    require_ont_read_groups,
    require_supported_platform,
    verify_gpu,
)
from .clair3_models import ModelMissing, choose_model, model_evidence
from .clair3_runtime import CLAIR3, MPS_CLAIR3, MpsSetup


def clair3_items(params: VcfClair3Params, reads: str | None) -> list[Item]:
    """Clair3's items; ``reads`` is the local BAM/CRAM input whose header
    chooses the model (None for a remote or missing one, never probed)."""
    try:
        require_supported_platform()
    except WGSExtractError as error:
        return [Item("platform", "Clair3 host", UNSUPPORTED, detail=str(error))]
    items = [Item("platform", "Clair3 host", RUNNABLE, detail="Clair3 runs here")]
    launcher = None
    if params.gpu:
        backend, extra = _gpu_items()
        items += [backend, *extra]
        launcher = backend.evidence.get("launcher")
    else:
        tool = tool_item(CLAIR3)
        tool.evidence["backend"] = "cpu"
        items.append(tool)
    header = get_bam_header(reads) if reads else ""
    if header:
        try:
            require_ont_read_groups(header)
        except WGSExtractError as error:
            items.append(
                Item("input", "ONT read groups", INCOMPATIBLE, detail=str(error))
            )
    items.append(_model_item(params.model, header, launcher))
    return items


def _gpu_items() -> tuple[Item, list[Item]]:
    """The --gpu backend, and the image and GPUs it runs on."""
    try:
        setup = verify_gpu()
    except WGSExtractError as error:
        backend = Item("backend", "Clair3 --gpu", status_of(error), detail=str(error))
        apple = sys.platform == "darwin"
        return backend, [] if apple else _cuda_evidence(None)
    if isinstance(setup, MpsSetup):
        return (
            Item(
                "backend",
                "Clair3 --gpu (Apple Silicon MPS)",
                RUNNABLE,
                path=str(setup.launcher),
                version=setup.version,
                evidence={
                    "launcher": str(setup.launcher),
                    **package_evidence(str(setup.launcher), MPS_CLAIR3),
                },
            ),
            [],
        )
    backend = Item(
        "backend",
        "Clair3 --gpu (NVIDIA CUDA image)",
        RUNNABLE,
        version=".".join(map(str, setup.version or ())) or None,
        evidence={"devices": list(setup.devices)},
    )
    return backend, _cuda_evidence(setup)


def _cuda_evidence(setup: GpuSetup | None) -> list[Item]:
    """The CUDA image and the GPUs, as far as this host reports them. Their
    failures are the backend's, which already blocks the run."""
    image = clair3_gpu_image()
    found = image_item(
        image,
        backend_ok=setup is not None,
        fix_text=f"Download it explicitly: docker pull {image} "
        "(or set clair3_gpu_image in config.toml).",
    )
    return [
        found,
        gpu_item(runs=setup is not None, devices=setup.devices if setup else ()),
    ]


def gpu_item(*, runs: bool, devices: tuple[int, ...] = ()) -> Item:
    """The NVIDIA GPUs and driver nvidia-smi reports; required when the
    backend ``runs`` on them (on ``devices``, or all), which it only does
    once its own checks of them passed."""
    gpus = gpu_memory() if get_tool_path("nvidia-smi") else ()
    driver = gpu_driver_version() if gpus else None
    return Item(
        "gpu",
        "NVIDIA GPU",
        RUNNABLE if runs and gpus else INSTALLED if gpus else MISSING,
        required=runs,
        detail=", ".join(f"GPU {g.index}: {g.name}" for g in gpus) or "none reported",
        version=driver,
        evidence={"devices": list(devices), "driver": driver},
    )


def _model_item(choice: str | None, header: str, launcher: object) -> Item:
    try:
        model = choose_model(choice, header, Path(str(launcher)) if launcher else None)
    except ModelMissing as error:
        return Item("model", choice or "Clair3 model", MISSING, detail=str(error))
    except WGSExtractError as error:
        return Item("model", choice or "Clair3 model", INCOMPATIBLE, detail=str(error))
    evidence = model_evidence(model)
    verified = evidence["verified"]
    item = Item("model", model.name, RUNNABLE, path=str(model), evidence=evidence)
    if verified is None:
        item.status = UNVERIFIED
        item.detail = "no pinned SHA-256: its integrity and chemistry are unverified"
    elif not verified:
        item.status = INCOMPATIBLE
        item.detail = "doesn't match its pinned SHA-256: corrupt, or another model"
        item.fix_text = f"Download it again from {evidence['source']}"
    else:
        item.detail = "matches its pinned SHA-256"
    return item
