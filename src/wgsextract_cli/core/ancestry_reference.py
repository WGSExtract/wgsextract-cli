"""The ancestry reference: LD-pruned common SNPs with HGDP+1KG allele
frequencies, shared by `qc ancestry` and (later) polygenic scores (#617).

One file, ``wgsextract-ancestry-hgdp1kg-v1.npz``, built by
scripts/ancestry_reference/ from gnomAD v3.1's HGDP+1KG release (CC0 1.0) and
published as a GitHub release asset. It is downloaded into
``<library>/ancestry/`` on first use (or ``ref ancestry --install``) and
checked against a pinned SHA-256 before every load. It holds:

- 187,417 autosomal biallelic SNPs on GRCh38, with their positions on GRCh37
  and T2T-CHM13 where they lift over one-to-one onto the same reference base;
- ADMIXTURE allele frequencies (P) of the alt allele in each component, at
  K=7 (continental) and K=12 (sub-continental), and each component's name and
  the reference populations that carry it most;
- a PCA of the 3,129 training samples: allele frequencies, SNP loadings, and
  the samples' scores with their population and region.
"""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.reference_assets import resolve_reference_library
from wgsextract_cli.core.utils import WGSExtractError

FOLDER = "ancestry"
NAME = "wgsextract-ancestry-hgdp1kg-v1.npz"
URL = (
    "https://github.com/WGSExtract/wgsextract-cli/releases/download/"
    f"ancestry-ref-1/{NAME}"
)
SHA256 = "c7fab2fac52e75900de0e5c239c1a480b239bf3a49ee3ecafe032aae437a5688"
KS = (7, 12)
# The reference's coordinate sets, by the CLI's canonical build names.
BUILDS = {"hg38": "GRCh38", "hg19": "GRCh37", "t2t": "CHM13"}
SOURCE = (
    "gnomAD v3.1 HGDP+1KG (Koenig et al. 2024, Genome Research; CC0 1.0), "
    "ADMIXTURE-model frequencies trained with ADAMIXTURE"
)


@dataclass(frozen=True)
class Component:
    name: str
    region: str
    top_populations: dict[str, float]


@dataclass(frozen=True)
class Sites:
    """The reference's sites on one build, in genome order."""

    index: np.ndarray  # rows of the reference's arrays
    chrom: np.ndarray  # 1..22
    pos: np.ndarray  # 1-based
    ref: list[str]
    alt: list[str]


class AncestryReference:
    def __init__(self, path: Path) -> None:
        with np.load(path, allow_pickle=False) as data:
            self._arrays = {name: data[name] for name in data.files}
        self.info: dict[str, Any] = json.loads(str(self._arrays.pop("info")))
        self.path = path

    @property
    def version(self) -> int:
        return int(self.info["version"])

    def sites(self, build: str) -> Sites:
        """The sites with coordinates on ``build`` (hg38, hg19 or t2t)."""
        name = BUILDS.get(build)
        if name is None:
            raise WGSExtractError(
                f"The ancestry reference has no coordinates for {build or 'an unknown build'}; "
                "it covers GRCh37/hg19, GRCh38 and T2T-CHM13."
            )
        pos = self._arrays[f"pos_{name}"]
        chrom = self._arrays[f"chrom_{name}"]
        index = np.flatnonzero(pos > 0)
        order = index[np.lexsort((pos[index], chrom[index]))]
        return Sites(
            order,
            chrom[order].astype(np.int64),
            pos[order].astype(np.int64),
            [b.decode() for b in self._arrays["ref"][order]],
            [b.decode() for b in self._arrays["alt"][order]],
        )

    def frequencies(self, k: int) -> np.ndarray:
        """(sites, K) alt-allele frequency of each component."""
        p: np.ndarray = self._arrays[f"P{k}"].astype(np.float64) / 65535.0
        return p

    def components(self, k: int) -> list[Component]:
        return [Component(**c) for c in self.info["components"][str(k)]]

    def pca(self) -> dict[str, np.ndarray]:
        """``frequency`` and ``loadings`` per site; ``scores``, ``population``
        and ``region`` per training sample."""
        return {
            key.removeprefix("pca_"): value
            for key, value in self._arrays.items()
            if key.startswith("pca_")
        }


def folder(reflib: str | None = None) -> Path:
    return Path(resolve_reference_library(reflib), FOLDER)


def path(reflib: str | None = None) -> Path:
    return folder(reflib) / NAME


def _sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed(reflib: str | None = None) -> bool:
    return path(reflib).is_file()


def install(
    reflib: str | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
) -> Path:
    """Download the reference if it isn't there; returns its path."""
    dest = path(reflib)
    if dest.is_file() and _sha256(dest) == SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not download_file(
        URL,
        str(dest),
        progress,
        cancel,
        expected_sha256=SHA256,
        resolve_release_digest=False,
    ):
        raise WGSExtractError(f"Could not download the ancestry reference {NAME}.")
    return dest


@functools.lru_cache(maxsize=2)
def _load(file: str, mtime: float) -> AncestryReference:
    return AncestryReference(Path(file))


def load(
    reflib: str | None = None,
    *,
    fetch: bool = True,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
) -> AncestryReference:
    """The verified reference, downloaded first if ``fetch`` and missing."""
    file = path(reflib)
    if not file.is_file():
        if not fetch:
            raise WGSExtractError(
                f"The ancestry reference is not installed ({file}). Run "
                "'wgsextract ref ancestry --install'."
            )
        install(reflib, progress, cancel)
    if _sha256(file) != SHA256:
        raise WGSExtractError(
            f"The ancestry reference {file} doesn't match its pinned SHA-256; "
            "delete it ('wgsextract ref ancestry --delete') and run again."
        )
    return _load(str(file), file.stat().st_mtime)
