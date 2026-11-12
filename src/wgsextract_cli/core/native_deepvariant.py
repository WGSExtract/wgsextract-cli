"""WGS Extract's DeepVariant build without Docker (native/deepvariant/README.md).

``wgse-deepvariant`` runs DeepVariant 1.10.0's WGS model natively and calls
the same sites, genotypes and filters as Google's x86-64 image: on Apple
Silicon with call_variants on the Metal GPU, and on Linux x86-64 with
call_variants on the CPU or, for ``--gpu``, an NVIDIA GPU through CUDA. ``vcf deepvariant`` uses it when it is on PATH; its weights
(converted from Google's image) are a separate download whose folder the
``deepvariant_weights`` setting names. ``deepvariant_native = "off"`` in
config.toml keeps the Docker (or Parabricks) path instead. Neither is ever
installed here, and the weights are checked against their published hashes
before every run.
"""

from __future__ import annotations

import hashlib
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

from wgsextract_cli.core.config import settings
from wgsextract_cli.core.native_support import is_arm64_docker_host
from wgsextract_cli.core.utils import WGSExtractError

BINARY = "wgse-deepvariant"
PIXI_ENVIRONMENT = "deepvariant"
# The oldest usable build: 0.2.0 is the first Linux one.
MINIMUM_VERSION = {"darwin": (0, 1, 0), "linux": (0, 2, 0)}
WEIGHTS_SETTING = "deepvariant_weights"
ENGINE_SETTING = "deepvariant_native"
ENGINE_CHOICES = ("auto", "off")
INSTALL_HINT = (
    "Install wgse-deepvariant (on a Mac a macos-deepvariant release on PATH, "
    "on Linux `pixi install -e deepvariant`) and its weights, and set "
    "deepvariant_weights in config.toml to the weights folder: see "
    "native/deepvariant/README.md "
    "(https://github.com/WGSExtract/wgsextract-cli/blob/main/native/deepvariant/"
    "README.md#installing)."
)
# The weights published with macos-deepvariant and linux-deepvariant releases
# (native/deepvariant/weights-sha256.txt, which the release builds check),
# by DeepVariant model: the big model's .dvw and the small model's layers.
WGS_WEIGHTS_SHA256 = {
    "wgs.dvw": "57fcefeaf230e7a795bb1fdbc275e5f02039f010de2ebcf8a9fde0cb9f006479",
    "wgs_small_weights/layer_0_bias.npy": (
        "a5b7d30e8983046c3fd6e40e2f10c10833fab80c756e7ea0dbc0ff53372b8627"
    ),
    "wgs_small_weights/layer_0_kernel.npy": (
        "ca6ff9b5e7ebaf4110e3052d4083bc46c2a9f3c717cb0b0c50bf0af5da2c18ee"
    ),
    "wgs_small_weights/layer_1_bias.npy": (
        "9d996fa6fdc2c5ea166cc989cad4b875a7d482d6d890750289f36aeed7f11df1"
    ),
    "wgs_small_weights/layer_1_kernel.npy": (
        "ac0e08a75c9e2bb64af8b0a9a541214d332e6628ee585356661392ed6bc3caa9"
    ),
    "wgs_small_weights/layer_2_bias.npy": (
        "f79e6f4c9bd45232f15ca4a1355b4e4d75ffc542deb1c376c1ca5152b553b093"
    ),
    "wgs_small_weights/layer_2_kernel.npy": (
        "6594ae0c241076db5c99b807a32915e1e46b82016ff181174e576eb2e2d59940"
    ),
}
WEIGHTS_SHA256 = {"WGS": WGS_WEIGHTS_SHA256}
_VERSION = re.compile(r"^\S+ (\d+)\.(\d+)\.(\d+) ")


def models() -> tuple[str, ...]:
    """The DeepVariant models wgse-deepvariant is qualified for: WGS. Its
    ONT_R104 make_examples doesn't match Google's yet (#719: 99 genotypes and
    77 filters of 3,405 sites differ on a 1 Mb HG002 slice)."""
    return tuple(WEIGHTS_SHA256)


@dataclass(frozen=True)
class NativeDeepVariant:
    """An installed wgse-deepvariant and its checked weights for one model."""

    path: str
    version: str
    weights: Path | None
    model_type: str = "WGS"
    # Linux: whether its ONNX Runtime has CUDA (the cuda129 package, not cpu).
    cuda: bool = False

    def _checked(self) -> Path:
        assert self.weights is not None, "weights not checked yet"
        return self.weights

    @property
    def checkpoint(self) -> Path:
        return self._checked() / f"{self.model_type.lower()}.dvw"

    @property
    def small_model(self) -> Path:
        return self._checked() / f"{self.model_type.lower()}_small_weights"

    def weight_files(self) -> dict[str, str]:
        """The weight files, by input role (for the step journal)."""
        return {
            f"weights:{name}": str(self._checked() / name)
            for name in WEIGHTS_SHA256[self.model_type]
        }


def default_batch_size() -> int:
    """call_variants' batch, as wgse-deepvariant picks it itself: on a Mac,
    for its memory (the GPU footprint is about 12 MiB an example); on Linux
    256, which needs about 3 GiB of an NVIDIA GPU's memory."""
    if sys.platform != "darwin":
        return 256
    gib = int(psutil.virtual_memory().total) >> 30
    return 128 if gib < 16 else 512 if gib < 32 else 1024 if gib < 64 else 2048


def installed() -> str | None:
    """wgse-deepvariant's path when it's on PATH of an Apple Silicon Mac or
    a Linux x86-64 computer (with deepvariant_weights set), or there in its
    Pixi environment, and the deepvariant_native setting doesn't turn it off."""
    setting = str(settings.get(ENGINE_SETTING) or "auto").lower()
    if setting not in ENGINE_CHOICES:
        raise WGSExtractError(
            f"{ENGINE_SETTING} must be one of {', '.join(ENGINE_CHOICES)}, "
            f"not {setting!r}."
        )
    if setting == "off":
        return None
    if sys.platform == "darwin" and not is_arm64_docker_host():
        return None
    if sys.platform != "darwin" and (
        not sys.platform.startswith("linux")
        or platform.machine().lower() not in {"x86_64", "amd64"}
        # Linux has Google's image and Parabricks: without the weights set,
        # an installed build isn't chosen.
        or not settings.get(WEIGHTS_SETTING)
    ):
        return None
    return shutil.which(BINARY) or (None if sys.platform == "darwin" else _pixi_build())


def _pixi_build() -> str | None:
    """wgse-deepvariant in the opt-in ``deepvariant`` Pixi environment
    (``pixi install -e deepvariant``, Linux x86-64), run by its path: its
    libraries load from the environment without activating it."""
    from wgsextract_cli.core.pixi_tools import pixi_env_prefix

    prefix = pixi_env_prefix(PIXI_ENVIRONMENT)
    binary = prefix / "bin" / BINARY if prefix else None
    return str(binary) if binary and binary.is_file() else None


def require(
    path: str, model_type: str = "WGS", *, weights: bool = True
) -> NativeDeepVariant:
    """The build at ``path`` with its weights for ``model_type`` (unless not
    ``weights``: the reads may choose another model); refuses an older build
    or weights that are missing or not the published ones."""
    try:
        probe = subprocess.run(
            [path, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise WGSExtractError(f"{BINARY} ({path}) doesn't run: {e}") from e
    first = (probe.stdout or "").splitlines()[:1]
    match = _VERSION.match(first[0]) if first else None
    if probe.returncode or not match:
        raise WGSExtractError(
            f"{BINARY} ({path}) didn't report its version: "
            f"{(probe.stderr or probe.stdout or '').strip()[-300:]}"
        )
    version = tuple(int(part) for part in match.groups())
    minimum = MINIMUM_VERSION["darwin" if sys.platform == "darwin" else "linux"]
    if version < minimum:
        raise WGSExtractError(
            f"{BINARY} {'.'.join(map(str, version))} is too old; "
            f"{'.'.join(map(str, minimum))} or later is needed. {INSTALL_HINT}"
        )
    # "native port — x86_64, Linux (call_variants: ONNX Runtime 1.31.1, CUDA or CPU)"
    lines = (probe.stdout or "").splitlines()
    cuda = len(lines) > 1 and "CUDA" in lines[1]
    return NativeDeepVariant(
        path,
        ".".join(map(str, version)),
        _checked_weights(model_type) if weights else None,
        model_type,
        cuda,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_weights(model_type: str) -> Path:
    configured = settings.get(WEIGHTS_SETTING)
    if not configured:
        raise WGSExtractError(
            f"{BINARY} is on PATH but {WEIGHTS_SETTING} isn't set. {INSTALL_HINT}"
        )
    weights = Path(str(configured)).expanduser()
    for name, expected in WEIGHTS_SHA256[model_type].items():
        path = weights / name
        if not path.is_file():
            raise WGSExtractError(
                f"DeepVariant weight file {path} is missing ({WEIGHTS_SETTING} "
                f"= {configured}). {INSTALL_HINT}"
            )
        found = _sha256(path)
        if found != expected:
            raise WGSExtractError(
                f"DeepVariant weight file {path} isn't the published one "
                f"(SHA-256 {found}, expected {expected}). {INSTALL_HINT}"
            )
    return weights
