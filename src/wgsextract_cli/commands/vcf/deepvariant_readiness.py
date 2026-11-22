"""``deps ready -- vcf deepvariant ...``: the backend a run would call with
(wgse-deepvariant, Google's run_deepvariant or image, the native ARM64
image, Parabricks or Google's GPU image) and what it needs, checked with the
validators the run uses, in the order ``run_deepvariant`` chooses: the
options' model (or the reads'), ``--gpu``, then the native build when it is
qualified for that model here, then a local run_deepvariant or Docker.
Images are inspected in the local engine, never pulled."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from wgsextract_cli.api.vcf import VcfDeepvariantParams
from wgsextract_cli.core import native_deepvariant
from wgsextract_cli.core.containers import docker_info
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.native_support import is_arm64_docker_host, require_native_tool
from wgsextract_cli.core.parabricks import (
    parabricks_image,
    verify_gpu_docker,
    verify_parabricks,
)
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
    terms,
)
from wgsextract_cli.core.utils import WGSExtractError

from .clair3_readiness import gpu_item
from .deepvariant import (
    _ULTIMA_GPU_SETUP,
    _gpu_model,
    _model_chosen,
    _model_type,
    _native_gpu_preflight,
    _native_model,
    _require_ont_x86,
    _verify_arm64_image,
    _verify_docker,
    _verify_docker_image,
    deepvariant_image,
    require_gpu_host,
)
from .deepvariant_pipeline.models import _ARM64_SETUP, _SETUP


def deepvariant_items(params: VcfDeepvariantParams, ref: str | None) -> list[Item]:
    try:
        require_native_tool("run_deepvariant")
    except WGSExtractError as error:
        return [Item("platform", "DeepVariant host", UNSUPPORTED, detail=str(error))]
    items = [Item("platform", "DeepVariant host", RUNNABLE)]
    model, chosen = _model(params, ref, items)
    checkpoint = str(params.checkpoint) if params.checkpoint else None
    try:
        backend = (_gpu_backend if params.gpu else _cpu_backend)(
            params, model, checkpoint
        )
    except WGSExtractError as error:
        backend = [_backend("DeepVariant", status_of(error), str(error))]
    for item in backend:
        if item.kind == "backend":
            item.evidence |= {"model_type": model, "model_chosen_by": chosen}
    return items + backend


def _model(
    params: VcfDeepvariantParams, ref: str | None, items: list[Item]
) -> tuple[str, str]:
    """The DeepVariant model the run calls with, and what chose it."""
    if _model_chosen(params) or not ref:
        return _model_type(params), "options" if _model_chosen(params) else "default"
    try:
        return _model_type(params, ref), "reads"
    except WGSExtractError as error:
        items.append(Item("input", "reads' platform", INCOMPATIBLE, detail=str(error)))
        return _model_type(params), "default"


def _backend(
    name: str,
    status: str = RUNNABLE,
    detail: str = "",
    *,
    path: str | None = None,
    version: str | None = None,
    evidence: dict[str, object] | None = None,
) -> Item:
    return Item(
        "backend",
        name,
        status,
        detail=detail,
        path=path,
        version=version,
        evidence=dict(evidence or {}),
    )


def _gpu_backend(
    params: VcfDeepvariantParams, model: str, checkpoint: str | None
) -> list[Item]:
    require_gpu_host()
    _gpu_model(replace(params, model_type=model))
    installed = native_deepvariant.installed()
    if installed and _native_model(installed, model, None):
        return _native_items(installed, model, gpu=True)
    if model != "ULTIMA":
        image = parabricks_image()
        try:
            verify_parabricks()
        except WGSExtractError as error:
            return [
                _backend("Parabricks", status_of(error), str(error)),
                _image_item(image, ok=False),
                gpu_item(runs=False),
            ]
        return [
            _backend("Parabricks", detail="DeepVariant on the GPU"),
            _image_item(image, ok=True),
            _bundled_model(model, "the Parabricks image"),
            gpu_item(runs=True),
        ]
    image = deepvariant_image(model, gpu=True)
    try:
        verify_gpu_docker(_ULTIMA_GPU_SETUP.format(image=image))
        _verify_docker_image(model, True)
    except WGSExtractError as error:
        return [
            _backend("Google's DeepVariant GPU image", status_of(error), str(error)),
            _image_item(image, ok=False),
            gpu_item(runs=False),
        ]
    return [
        _backend("Google's DeepVariant GPU image"),
        _image_item(image, ok=True),
        _bundled_model(model, "the image"),
        gpu_item(runs=True),
    ]


def _cpu_backend(
    params: VcfDeepvariantParams, model: str, checkpoint: str | None
) -> list[Item]:
    native = native_deepvariant.installed()
    if native and _native_model(native, model, checkpoint):
        return _native_items(native, model, gpu=False)
    if is_arm64_docker_host():
        image = deepvariant_image(model)
        try:
            _verify_arm64_image(model, checkpoint)
        except WGSExtractError as error:
            name = "DeepVariant native ARM64 image"
            return [
                _backend(name, status_of(error), str(error)),
                _image_item(image, ok=False, fix_text=_ARM64_SETUP),
            ]
        return [
            _backend("DeepVariant native ARM64 image"),
            _image_item(image, ok=True, fix_text=_ARM64_SETUP),
            _model_files(model, checkpoint, "the image"),
        ]
    _require_ont_x86(model)
    if get_tool_path("run_deepvariant") and model != "ULTIMA":
        tool = tool_item("run_deepvariant")
        tool.kind = "backend"
        return [tool, _model_files(model, checkpoint, "run_deepvariant")]
    image = deepvariant_image(model)
    try:
        _verify_docker(model)
        _verify_docker_image(model)
    except WGSExtractError as error:
        name = "Google's DeepVariant image"
        return [
            _backend(name, status_of(error), str(error)),
            _image_item(image, ok=False),
        ]
    engine = docker_info(_SETUP.format(image=image)).strip().strip('"')
    return [
        _backend("Google's DeepVariant image", version=engine or None),
        _image_item(image, ok=True),
        _model_files(model, checkpoint, "the image"),
    ]


def _native_items(path: str, model: str, *, gpu: bool) -> list[Item]:
    """wgse-deepvariant, its weights for ``model`` and, with --gpu, CUDA."""
    try:
        native = native_deepvariant.require(path, model, weights=False)
        if gpu:
            _native_gpu_preflight(native)
    except WGSExtractError as error:
        return [_backend("wgse-deepvariant", status_of(error), str(error), path=path)]
    backend = _backend(
        "wgse-deepvariant",
        detail="DeepVariant without Docker" + (" on CUDA" if gpu else ""),
        path=path,
        version=native.version,
        evidence={"cuda": native.cuda, **package_evidence(path, "wgse-deepvariant")},
    )
    items = [backend, _weights_item(model)]
    if gpu:
        items.append(gpu_item(runs=True))
    return items


def _weights_item(model: str) -> Item:
    evidence = native_deepvariant.weights_evidence(model)
    found = evidence["sha256"]
    item = Item(
        "model",
        f"wgse-deepvariant {model} weights",
        RUNNABLE,
        path=evidence["path"],
        evidence=evidence,
        detail="match the published SHA-256",
    )
    hint = native_deepvariant.INSTALL_HINT
    if evidence["verified"] is None:
        item.status, item.detail = MISSING, f"{evidence['setting']} isn't set"
        item.fix_text = hint
    elif not evidence["verified"]:
        absent = [name for name, digest in found.items() if digest is None]
        item.status = MISSING if absent else INCOMPATIBLE
        item.detail = (
            f"missing: {', '.join(absent)}"
            if absent
            else "don't match the published SHA-256"
        )
        item.fix_text = hint
    return item


def _image_item(image: str, *, ok: bool, fix_text: str | None = None) -> Item:
    return image_item(
        image,
        backend_ok=ok,
        fix_text=fix_text or f"Download it explicitly: docker pull {image}",
    )


def _bundled_model(model: str, where: str) -> Item:
    return Item(
        "model",
        f"DeepVariant {model} model",
        INSTALLED,
        detail=f"inside {where}; its identity is {where}'s",
        evidence={"terms": terms()},
    )


def _model_files(model: str, checkpoint: str | None, where: str) -> Item:
    """The model the image or release calls with, or the user's checkpoint."""
    if not checkpoint:
        return _bundled_model(model, where)
    found = any(Path(checkpoint).parent.glob(Path(checkpoint).name + "*"))
    return Item(
        "model",
        checkpoint,
        UNVERIFIED if found else MISSING,
        detail="a custom checkpoint has no pinned SHA-256"
        if found
        else "the checkpoint's files don't exist",
        path=checkpoint,
        evidence={"terms": terms()},
    )
