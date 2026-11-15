"""Ancestry-adjusted percentiles for `score pgs` (issue #617, Phase 2).

The reference file ``wgsextract-pgs-hgdp1kg-v1.npz`` (built by
scripts/pgs_reference/, published as the GitHub release ``pgs-ref-1``) holds,
for each curated score: which rows of the pinned Catalog scoring file the
HGDP+1KG reference panel was scored on (the calibration set), those rows'
effect-allele frequencies in the panel, the scores of the 3,129 training
samples of the `qc ancestry` PCA, and the fitted adjustment models.

The adjustments are pgscatalog.calc 0.3.1's (``_ancestry/tools.py``):

- the most similar reference region, by Mahalanobis distance on PCs 1-5, and
  the score's percentile and Z among that region's reference samples
  ("empirical");
- Z_norm1: the score minus a linear prediction from PCs 1-4, over the
  residual SD (Khera et al. 2019);
- Z_norm2: the score minus a mean, over an SD, both predicted from PCs 1-4
  by a joint maximum-likelihood fit (Khan et al. 2022; eMERGE GIRA). Its
  normal percentile is the headline ancestry-adjusted percentile.

A user's calibrated score sums the calibration rows only. Rows the user has
no genotype for are mean-imputed with the frequency in the reference region
the user is most similar to (pgsc_calc fills in missing genotypes from
reference frequencies, plink2 ``--read-freq``); the share of the score's
variance that is imputed is reported and gated.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

from wgsextract_cli.core.download_progress import CancelEvent, ProgressCallback
from wgsextract_cli.core.ref_library.downloads import download_file
from wgsextract_cli.core.reference_assets import resolve_reference_library
from wgsextract_cli.core.utils import WGSExtractError

FOLDER = "pgs"
NAME = "wgsextract-pgs-hgdp1kg-v1.npz"
URL = f"https://github.com/WGSExtract/wgsextract-cli/releases/download/pgs-ref-1/{NAME}"
SHA256 = "d73b7243825f33290484ff0aea455f21c0fc3bbd60380dc665a31003134d083d"
BUILD = "GRCh38"
# The Catalog scoring files the reference was built from, by SHA-256. Only
# these get a percentile, so other scores never download the reference.
SCORES: dict[str, str] = {
    "ed5dcd5359213364a182dd7972edf524cc3e161f8332ca54ca5515c95cbf8d2a": "PGS000018",
    "68d7894cec4ddc5294c3b24294aae021b8187350ba589ea8acde8ba88d33c191": "PGS000004",
    "163c0409f4059ab302873e82f9f568be5be4b361cc4c4a8526b6296cbbe391e7": "PGS000297",
    "73d7b4a7cdb93355e86c268b42e4b5014ddea4e4f02cceaff8a054a2ab909522": "PGS000020",
    "7c0d06b5b3a932fc63de24c75081c85391ae137b106c27f67e912b09c58c4604": "PGS000027",
    "ab86088702a224882cc36abe908f00f92ba4828d69d3dd6713f3e216b50fc9ce": "PGS000038",
    "54818ee38145e2656e9099a313e1c5f9aa0b57d98f02a863c02f9c0e6442cd52": "PGS000063",
    "3a3282e56cd2fe9f76d65c8eeae78ff5cbbc78810ed844d88e1185d62c6504f2": "PGS000065",
    "4ce5ebb41c4a240208b778354953b86a90e346b38df11fe33febb038c82760aa": "PGS000067",
    "3472bfce02685c1de9cb34bdccc07efcb0c5560b50cbbc5f049bb952bc173204": "PGS000070",
    "e08b794c10f1ca4628c7f8de4082d8ac0182079f72f8138ef19052eaa0b4e358": "PGS000087",
}
# Validated bounds (docs/pgs.md, measured in #617): at most this share of the
# calibrated score's variance may come from imputed rows; at least this share
# of the ancestry sites must have a genotype; and pgscatalog.calc's
# Mahalanobis threshold for a low-confidence population match.
MAX_IMPUTED_VARIANCE = 0.05
MIN_ANCESTRY_SITES = 0.9
MIN_POPULATION_P = 1e-10


@dataclass
class Calibrated:
    total: float
    matched: int
    imputed: int
    imputed_share: float  # of the calibrated score's variance


class Accumulator:
    """One sample's genotypes at a score's calibration rows, collected as
    Phase 1's scorer visits the scoring file."""

    def __init__(
        self, mask: np.ndarray, frequency: np.ndarray, regions: list[str]
    ) -> None:
        import numpy as np

        self.mask = mask  # bool per scoring-file row
        self.frequency = (
            frequency  # (calibration rows, regions) effect-allele frequency
        )
        self.regions = regions
        self.weight = np.zeros(frequency.shape[0])
        self.dosage = np.full(frequency.shape[0], np.nan)
        self._next = 0

    def add(self, row: int, weight: float, dosage: int | None) -> None:
        """Row ``row`` (0-based) with its weight and the sample's effect-allele
        dosage (None when unmatched)."""
        if row >= self.mask.size or not self.mask[row]:
            return
        self.weight[self._next] = weight
        if dosage is not None:
            self.dosage[self._next] = dosage
        self._next += 1

    def result(self, region: str) -> Calibrated:
        """The calibrated sum, with unmatched rows at the mean dosage of the
        reference region the sample is most similar to."""
        import numpy as np

        f = self.frequency[:, self.regions.index(region)]
        missing = np.isnan(self.dosage)
        dosage = np.where(missing, 2 * f, self.dosage)
        variance = self.weight**2 * 2 * f * (1 - f)
        total_variance = float(variance.sum())
        return Calibrated(
            total=float(self.weight @ dosage),
            matched=int((~missing).sum()),
            imputed=int(missing.sum()),
            imputed_share=float(variance[missing].sum()) / total_variance
            if total_variance
            else 1.0,
        )


class PgsReference:
    def __init__(self, path: Path) -> None:
        import numpy as np

        with np.load(path, allow_pickle=False) as data:
            self._arrays = {name: data[name] for name in data.files}
        self.info: dict[str, Any] = json.loads(str(self._arrays.pop("info")))
        self.path = path

    @property
    def version(self) -> int:
        return int(self.info["version"])

    def score_info(self, pgs_id: str) -> dict[str, Any] | None:
        info: dict[str, Any] | None = self.info["scores"].get(pgs_id)
        return info

    def accumulator(self, pgs_id: str) -> Accumulator:
        import numpy as np

        rows = self.info["scores"][pgs_id]["rows"]
        mask = np.unpackbits(self._arrays[f"{pgs_id}_rows"], count=rows).astype(bool)
        frequency = self._arrays[f"{pgs_id}_frequency"].astype(np.float64) / 65535.0
        return Accumulator(mask, frequency, list(self.info["frequency_regions"]))

    def training_regions(self, ancestry_regions: np.ndarray) -> np.ndarray:
        """The region of each training sample, from the ancestry reference's
        PCA samples (some of which the PGS reference panel lacks)."""
        regions: np.ndarray = ancestry_regions[self._arrays["training_rows"]]
        return regions

    def training_scores(self, pgs_id: str) -> np.ndarray:
        scores: np.ndarray = self._arrays[f"{pgs_id}_scores"]
        return scores


def _chi2_sf(x: float, df: int) -> float:
    """chi-square survival function for an even number of degrees of freedom."""
    if df % 2:
        raise ValueError("odd degrees of freedom")
    half = x / 2
    term, total = 1.0, 1.0
    for i in range(1, df // 2):
        term *= half / i
        total += term
    return math.exp(-half) * total


def _percentile_of_score(values: np.ndarray, score: float) -> float:
    """scipy.stats.percentileofscore(values, score, kind="rank"), counting
    reference scores within rounding of ``score`` as ties: the reference sums
    carry plink2's six significant digits, and a short score's sums are few
    distinct values, so an exact comparison would move a tied score by half
    its tie group."""
    tolerance = 1e-5 * max(1.0, abs(score))
    left = int((values < score - tolerance).sum())
    right = int((values <= score + tolerance).sum())
    return float((left + right + (1 if right > left else 0)) * 50.0 / values.size)


def similarity(reference: PgsReference, pcs: np.ndarray) -> dict[str, Any]:
    """The reference region the sample is most similar to (pgscatalog.calc's
    Mahalanobis comparison on PCs 1-5), and how similar it is to the whole
    reference."""
    import numpy as np

    info = reference.info
    k5 = int(info["n_popcomp"])
    x5 = np.asarray(pcs[:k5], dtype=np.float64)
    p: dict[str, float] = {}
    for name, population in info["populations"].items():
        centred = x5 - np.asarray(population["location"])
        p[name] = _chi2_sf(
            float(centred @ np.asarray(population["precision"]) @ centred), k5 - 1
        )
    p_all = p.pop("ALL")
    closest = max(p, key=lambda name: p[name])
    return {
        "most_similar_population": closest,
        "population_similarity_p": p[closest],
        "population_low_confidence": p[closest] < MIN_POPULATION_P,
        "reference_similarity_p": p_all,
    }


def adjust(
    reference: PgsReference,
    pgs_id: str,
    score: float,
    pcs: np.ndarray,
    training_regions: np.ndarray,
    closest: str,
) -> dict[str, Any]:
    """pgscatalog.calc's empirical, mean and mean+var adjustments of one
    calibrated score at the sample's PCs."""
    import numpy as np

    model = reference.info["scores"][pgs_id]["model"]
    k4 = int(reference.info["n_normalization"])
    region_scores = reference.training_scores(pgs_id)[training_regions == closest]
    region = model["regions"][closest]
    xn = (np.asarray(pcs[:k4], dtype=np.float64) - model["pc_mean"]) / model["pc_std"]
    y = score - model["pgs_offset"]
    c1 = np.asarray(model["norm1_coef"])
    z1 = (y - (c1[0] + xn @ c1[1:])) / model["norm1_resid_std"]
    mu, var = np.asarray(model["norm2_mu_coef"]), np.asarray(model["norm2_var_coef"])
    z2 = (y - (mu[0] + xn @ mu[1:])) / math.sqrt(math.exp(var[0] + xn @ var[1:]))
    return {
        "empirical_percentile": _percentile_of_score(region_scores, score),
        "empirical_z": (score - region["mean"]) / region["std"],
        "empirical_reference_samples": region["n"],
        "z_norm1": float(z1),
        "z_norm2": float(z2),
        "adjusted_percentile": normal_percentile(float(z2)),
    }


def normal_percentile(z: float) -> float:
    return 50.0 * (1.0 + math.erf(z / math.sqrt(2.0)))


def library_for(ref: str | None) -> str:
    from wgsextract_cli.core.reference_resolver import library_roots

    return resolve_reference_library(library_roots(ref).resource_root(FOLDER))


def path(reflib: str | None = None) -> Path:
    return Path(resolve_reference_library(reflib), FOLDER) / NAME


def _sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@functools.lru_cache(maxsize=2)
def _load(file: str, mtime: float) -> PgsReference:
    return PgsReference(Path(file))


def load(
    reflib: str | None = None,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
) -> PgsReference:
    """The verified reference, downloaded first when missing."""
    file = path(reflib)
    if not (file.is_file() and _sha256(file) == SHA256):
        file.parent.mkdir(parents=True, exist_ok=True)
        if not download_file(
            URL,
            str(file),
            progress,
            cancel,
            expected_sha256=SHA256,
            resolve_release_digest=False,
        ):
            raise WGSExtractError(f"Could not download the PGS reference {NAME}.")
        if _sha256(file) != SHA256:
            raise WGSExtractError(
                f"The PGS reference {file} doesn't match its pinned SHA-256; "
                "delete it and run again."
            )
    return _load(str(file), file.stat().st_mtime)
