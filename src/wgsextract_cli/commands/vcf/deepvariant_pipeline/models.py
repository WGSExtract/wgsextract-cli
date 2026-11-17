"""DeepVariant model selection and supported container images."""

import logging
import os
import platform
import re
import sys

from wgsextract_cli.api.vcf import VcfDeepvariantParams
from wgsextract_cli.core import ultima
from wgsextract_cli.core.alignment_metadata import get_bam_header
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.containers import (
    docker,
    docker_info,
    host_path,
    require_image,
)
from wgsextract_cli.core.native_support import is_arm64_docker_host
from wgsextract_cli.core.region_selection import (
    is_region_file,
    region_items,
)
from wgsextract_cli.core.sequencing import input_platform, refuse_mixed_platforms
from wgsextract_cli.core.utils import UsageError, WGSExtractError, run_command

# Google's image; Bioconda's deepvariant has shipped no DeepVariant since 1.8.
DEFAULT_IMAGE = "google/deepvariant:1.10.0"
DEFAULT_ARM64_IMAGE = "localhost/wgsextract-deepvariant:1.10.0-arm64"
# Google's experimental image with the ULTIMA model (google/deepvariant#1100),
# until a release has it.
DEFAULT_ULTIMA_IMAGE = "google/deepvariant:gh1100-ultima"
# Its GPU build, for --gpu (Parabricks has no ULTIMA model), pinned to the
# build validated in #358.
DEFAULT_ULTIMA_GPU_IMAGE = (
    "google/deepvariant:gh1100-ultima-gpu"
    "@sha256:f5572ef9b996c619c9e73a3f3391f894af72bf868d27578d9a4985298e75a1f7"
)
_SETUP = (
    "DeepVariant runs from Google's Docker image on x86-64 Linux and Intel "
    "Macs: install Docker, then docker pull {image}. "
    "Or put Google's run_deepvariant on PATH."
)


def deepvariant_image(model_type: str = "WGS", gpu: bool = False) -> str:
    if model_type == "ULTIMA" and gpu:
        return str(
            settings.get("deepvariant_ultima_gpu_image") or DEFAULT_ULTIMA_GPU_IMAGE
        )
    if model_type == "ULTIMA":
        return str(settings.get("deepvariant_ultima_image") or DEFAULT_ULTIMA_IMAGE)
    default = DEFAULT_ARM64_IMAGE if is_arm64_docker_host() else DEFAULT_IMAGE
    return str(settings.get("deepvariant_image") or default)


_ARM64_SETUP = (
    "Build the native CPU DeepVariant 1.10.0 image on Linux ARM64 with "
    "packaging/deepvariant-arm64/build.sh (on a Mac, build it there and move "
    "it with docker save / docker load; see docs/deepvariant-arm64.md); "
    "set deepvariant_image in config.toml if you "
    "use another local tag. Dependencies and images must already be installed. "
    "Google's x86 image, emulation and unaudited PATH launchers are not supported."
)
_ARM64_ALTERNATIVES = (
    "Otherwise call variants with vcf clair3 (ONT reads), vcf snp and vcf indel "
    "(bcftools, short reads), or run DeepVariant on an x86-64 host."
)


def _require_arm64_wgs(model_type: str, checkpoint: str | None = None) -> None:
    if model_type != "WGS" or checkpoint:
        selection = "--checkpoint" if checkpoint else f"model {model_type}"
        raise WGSExtractError(
            f"DeepVariant {selection} is not qualified on ARM64; "
            f"only the default WGS model is qualified. {_ARM64_ALTERNATIVES}"
        )


def _verify_arm64_image(model_type: str = "WGS", checkpoint: str | None = None) -> str:
    _require_arm64_wgs(model_type, checkpoint)
    docker_info(_ARM64_SETUP)
    architecture = docker_info(_ARM64_SETUP, "{{.Architecture}}").strip().lower()
    if architecture not in {"aarch64", "arm64"}:
        raise WGSExtractError(
            "DeepVariant requires a native ARM64 Docker engine on ARM64; "
            "an x64 engine or emulation is not supported."
        )
    image = deepvariant_image(model_type)
    require_image(
        image,
        f"Native DeepVariant image isn't installed: {image}. {_ARM64_SETUP} "
        f"{_ARM64_ALTERNATIVES}",
    )
    found = _image_platform(image, _ARM64_SETUP)
    _refuse_emulated_amd64(image, architecture, found)
    if found != "linux/arm64":
        raise WGSExtractError(
            f"DeepVariant image {image} must be linux/arm64; "
            "x86 images and emulation are not supported."
        )
    return image


def _image_platform(image: str, setup: str) -> str:
    """The image's ``os/architecture`` (``linux/amd64``), or "" if unknown."""
    inspected = run_command(
        [docker(setup), "image", "inspect", "--format",
         "{{.Os}}/{{.Architecture}}", image],
        capture_output=True,
        check=False,
    )  # fmt: skip
    return (inspected.stdout or "").strip() if not inspected.returncode else ""


def _refuse_emulated_amd64(image: str, engine: str, found: str) -> None:
    """An amd64 image on an ARM64 engine aborts in its first stage (#665)."""
    if engine in {"aarch64", "arm64"} and found == "linux/amd64":
        raise WGSExtractError(
            f"DeepVariant image {image} is linux/amd64 and can't run on this "
            "ARM64 Docker engine: Google publishes DeepVariant for x86-64 only, "
            "and Docker's x86-64 emulation (including Docker Desktop on Apple "
            "Silicon) lacks the AVX instructions its TensorFlow needs, so it "
            f"aborts (exit 134). {_ARM64_SETUP} {_ARM64_ALTERNATIVES}"
        )


def _model_type(
    params: VcfDeepvariantParams,
    ref: str | None = None,
    platform: str | None = None,
) -> str:
    """Explicit model selectors take precedence over platform defaults: the
    detected ``platform``, else (given ``ref``) the reads' own."""
    model, ont = params.model_type, params.ont
    if (ont or model == "ONT_R104") and (
        params.pacbio or params.wes or (ont and model not in (None, "ONT_R104"))
    ):
        raise UsageError(
            "--ont/--model-type ONT_R104 cannot be combined with "
            "--pacbio, --wes or a different --model-type."
        )
    if ont:
        return "ONT_R104"
    if params.pacbio:
        return "PACBIO"
    if model:
        return str(model)
    if params.wes:
        return "WES"
    input_file = str(params.input)
    detected = platform
    if detected is None and ref:
        detected = input_platform(input_file, ref)
        if detected is None:
            refuse_mixed_platforms(input_file, ref, "--model-type (or --ont/--pacbio)")
    if detected == "ont":
        logging.info(
            "ONT reads: calling with DeepVariant's ONT_R104 model (R10.4.1 only)."
        )
        return "ONT_R104"
    if ref and ultima.alignment_flow_data(input_file, ref).flow_aware:
        logging.info("Ultima reads: calling with DeepVariant's ULTIMA model.")
        return "ULTIMA"
    return "WGS"


def _require_ont_x86(model_type: str) -> None:
    if model_type == "ONT_R104" and platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        raise WGSExtractError(
            "DeepVariant ONT_R104 requires x86-64; it is not qualified on ARM64, "
            "including Apple Silicon. Use vcf clair3, or an x86-64 host."
        )


def _warn_ont_chemistry(alignment: str, ref: str) -> None:
    metadata = []
    for line in get_bam_header(alignment, ref).splitlines():
        if line.startswith("@PG\t"):
            metadata.append(line)
        elif line.startswith("@RG\t"):
            metadata.extend(
                field for field in line.split("\t") if field.startswith("DS:")
            )
    if re.search(
        r"(?<![a-z0-9])r9[._-]4(?:[._-]1)?(?![a-z0-9])",
        "\n".join(metadata),
        re.IGNORECASE,
    ):
        logging.warning(
            "R9.4.1/R9.4 basecaller metadata detected: this chemistry is unsupported "
            "by DeepVariant ONT_R104, which supports R10.4.1 only. "
            "Do not interpret these calls as validated ONT results."
        )


def _verify_docker(model_type: str = "WGS") -> None:
    """Docker runs here (Windows can't run DeepVariant's Linux image)."""
    setup = _SETUP.format(image=deepvariant_image(model_type))
    if sys.platform == "win32":
        raise WGSExtractError(f"DeepVariant isn't available on Windows. {setup}")
    docker_info(setup)


def _verify_docker_image(model_type: str, gpu: bool = False) -> str:
    image = deepvariant_image(model_type, gpu)
    setting = (
        "deepvariant_ultima_gpu_image"
        if gpu
        else "deepvariant_ultima_image"
        if model_type == "ULTIMA"
        else "deepvariant_image"
    )
    require_image(
        image,
        f"The DeepVariant image isn't downloaded: docker pull {image} "
        f"(set {setting} in config.toml for another version).",
    )
    setup = _SETUP.format(image=image)
    engine = docker_info(setup, "{{.Architecture}}").strip().lower()
    _refuse_emulated_amd64(image, engine, _image_platform(image, setup))
    return image


def _dv_env() -> dict[str, str]:
    # DeepVariant runs its own Python; don't leak ours into it.
    env = dict(os.environ)
    for var in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(var, None)
    return env


def _container_regions(region: str) -> str:
    """``region`` with its BED files at the paths mounted in the container."""
    return " ".join(
        host_path(p) if is_region_file(p) else p
        for p in region_items(region, commas=False)
    )
