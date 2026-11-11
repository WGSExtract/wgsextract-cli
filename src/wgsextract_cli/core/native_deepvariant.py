"""WGS Extract's Apple Silicon DeepVariant build (native/deepvariant/README.md).

``wgse-deepvariant`` runs DeepVariant 1.10.0's WGS model natively on Apple
Silicon, with call_variants on the GPU, and calls the same sites, genotypes
and filters as Google's x86-64 image. ``vcf deepvariant`` uses it on an Apple
Silicon Mac when it is on PATH; its weights (converted from Google's image) are
a separate download whose folder the ``deepvariant_weights`` setting names.
Neither is ever installed here, and the weights are checked against their
published hashes before every run.
"""

from __future__ import annotations

import hashlib
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
MINIMUM_VERSION = (0, 1, 0)
WEIGHTS_SETTING = "deepvariant_weights"
INSTALL_HINT = (
    "Install wgse-deepvariant and its WGS weights from a macos-deepvariant "
    "release, put wgse-deepvariant on PATH and set deepvariant_weights in "
    "config.toml to the weights folder: see native/deepvariant/README.md "
    "(https://github.com/WGSExtract/wgsextract-cli/blob/main/native/deepvariant/"
    "README.md#installing)."
)
# The weights published with macos-deepvariant releases
# (native/deepvariant/weights-sha256.txt, which the release build checks).
WEIGHTS_SHA256 = {
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
_VERSION = re.compile(r"^\S+ (\d+)\.(\d+)\.(\d+) ")


@dataclass(frozen=True)
class NativeDeepVariant:
    """An installed wgse-deepvariant and its checked weights."""

    path: str
    version: str
    weights: Path

    @property
    def checkpoint(self) -> Path:
        return self.weights / "wgs.dvw"

    @property
    def small_model(self) -> Path:
        return self.weights / "wgs_small_weights"

    def weight_files(self) -> dict[str, str]:
        """The weight files, by input role (for the step journal)."""
        return {f"weights:{name}": str(self.weights / name) for name in WEIGHTS_SHA256}


def default_batch_size() -> int:
    """call_variants' batch for this Mac's memory, as wgse-deepvariant picks
    it itself: its GPU footprint is about 12 MiB an example."""
    gib = int(psutil.virtual_memory().total) >> 30
    return 128 if gib < 16 else 512 if gib < 32 else 1024 if gib < 64 else 2048


def installed() -> str | None:
    """wgse-deepvariant's path when it's on PATH of an Apple Silicon Mac."""
    if sys.platform != "darwin" or not is_arm64_docker_host():
        return None
    return shutil.which(BINARY)


def require(path: str) -> NativeDeepVariant:
    """The build at ``path`` with its weights; refuses an older build or
    weights that are missing or not the published ones."""
    try:
        probe = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=30
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
    if version < MINIMUM_VERSION:
        raise WGSExtractError(
            f"{BINARY} {'.'.join(map(str, version))} is too old; "
            f"{'.'.join(map(str, MINIMUM_VERSION))} or later is needed. {INSTALL_HINT}"
        )
    return NativeDeepVariant(path, ".".join(map(str, version)), _checked_weights())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_weights() -> Path:
    configured = settings.get(WEIGHTS_SETTING)
    if not configured:
        raise WGSExtractError(
            f"{BINARY} is on PATH but {WEIGHTS_SETTING} isn't set. {INSTALL_HINT}"
        )
    weights = Path(str(configured)).expanduser()
    for name, expected in WEIGHTS_SHA256.items():
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
