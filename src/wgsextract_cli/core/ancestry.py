"""Admixture-style ancestry proportions of one sample, against fixed reference
allele frequencies.

ADMIXTURE's model: at each site j the sample's alleles are drawn with
frequency f_j = sum_k q_k p_jk, where p_jk is the frequency of the counted
allele in reference component k and q are the sample's proportions. With P
fixed (trained offline on the reference panel, see
scripts/ancestry_reference/) the likelihood of q is concave and EM converges
to its maximum. Genotype likelihoods take the place of hard calls, so a
low-coverage sample contributes what its reads say and no more; the genotype
is a second latent variable of the same EM.

Confidence intervals come from a block bootstrap over sites: contiguous runs
of sites are resampled, so residual linkage between neighbours widens them as
it should.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# P is clipped away from 0 and 1, as ADMIXTURE does, so one unexpected allele
# can't rule a component out.
_P_FLOOR = 1e-5
_TOLERANCE = 1e-6  # log-likelihood units
_INNER_ITERATIONS = 1000
_MAX_ITERATIONS = 100


@dataclass(frozen=True)
class Projection:
    proportions: np.ndarray  # (K,)
    lower: np.ndarray  # (K,) 2.5th bootstrap percentile
    upper: np.ndarray  # (K,) 97.5th
    sites: int
    log_likelihood: float
    iterations: int


def genotype_likelihoods(
    ref_reads: np.ndarray, alt_reads: np.ndarray, error: float = 0.01
) -> np.ndarray:
    """(n, 3) likelihoods of 0, 1 and 2 copies of the alt allele, from read
    counts, scaled so each row's largest is 1."""
    ref = np.asarray(ref_reads, dtype=np.float64)[:, None]
    alt = np.asarray(alt_reads, dtype=np.float64)[:, None]
    p_alt = np.array([error, 0.5, 1.0 - error])[None, :]
    log = alt * np.log(p_alt) + ref * np.log1p(-p_alt)
    scaled: np.ndarray = np.exp(log - log.max(axis=1, keepdims=True))
    return scaled


def hard_call_likelihoods(dosage: np.ndarray, error: float = 1e-3) -> np.ndarray:
    """(n, 3) likelihoods for called genotypes (0, 1 or 2 alt alleles); a small
    error keeps a miscall from ruling a component out."""
    dosage = np.asarray(dosage, dtype=np.int64)
    likelihoods = np.full((dosage.size, 3), error)
    likelihoods[np.arange(dosage.size), dosage] = 1.0
    return likelihoods


class _Model:
    """The log-likelihood of q at fixed P and site weights, with its gradient
    and Hessian. At each site the probability of the data is a quadratic in
    the site's frequency f = P q: a + b f + c f^2."""

    def __init__(
        self, p: np.ndarray, likelihoods: np.ndarray, weights: np.ndarray | None
    ) -> None:
        self.p = p
        l0, l1, l2 = (likelihoods[:, i] for i in range(3))
        self.a = l0
        self.b = 2.0 * (l1 - l0)
        self.c = l0 - 2.0 * l1 + l2
        self.weights = np.ones(p.shape[0]) if weights is None else weights

    def log_likelihood(self, q: np.ndarray) -> float:
        f = self.p @ q
        return float(self.weights @ np.log(self.a + f * (self.b + self.c * f)))

    def derivatives(self, q: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        f = self.p @ q
        site = self.a + f * (self.b + self.c * f)
        slope = (self.b + 2.0 * self.c * f) / site
        first = self.weights * slope
        second = self.weights * (2.0 * self.c / site - slope * slope)
        log_likelihood = float(self.weights @ np.log(site))
        return log_likelihood, self.p.T @ first, (self.p.T * second) @ self.p


def _simplex(v: np.ndarray) -> np.ndarray:
    """Euclidean projection onto the probability simplex (Duchi et al. 2008)."""
    u = np.sort(v)[::-1]
    cumulative = np.cumsum(u) - 1.0
    rho = np.nonzero(u - cumulative / np.arange(1, v.size + 1) > 0)[0][-1]
    projected: np.ndarray = np.maximum(v - cumulative[rho] / (rho + 1.0), 0.0)
    return projected


def _newton_target(q: np.ndarray, grad: np.ndarray, hess: np.ndarray) -> np.ndarray:
    """The maximum over the simplex of the local quadratic model, by
    accelerated projected gradient (K is small, so this costs no data pass)."""
    eigen = np.linalg.eigvalsh(hess)
    shift = max(0.0, eigen[-1]) + 1e-9 * max(1.0, -eigen[0])
    model = hess - shift * np.eye(q.size)
    step = 1.0 / (shift - eigen[0] + 1e-12)
    x = y = q.copy()
    t = 1.0
    for _ in range(_INNER_ITERATIONS):
        new = _simplex(y + step * (grad + model @ (y - q)))
        t_new = (1 + np.sqrt(1 + 4 * t * t)) / 2
        y = new + (t - 1) / t_new * (new - x)
        if np.abs(new - x).max() < 1e-10:
            x = new
            break
        x, t = new, t_new
    target: np.ndarray = x
    return target


def _maximise(
    p: np.ndarray, likelihoods: np.ndarray, q: np.ndarray, weights: np.ndarray | None
) -> tuple[np.ndarray, float, int]:
    """Projected Newton with a backtracking line search; the likelihood is
    concave in q for hard calls and in practice for likelihoods."""
    model = _Model(p, likelihoods, weights)
    iterations = 0
    while iterations < _MAX_ITERATIONS:
        iterations += 1
        log_likelihood, grad, hess = model.derivatives(q)
        direction = _newton_target(q, grad, hess) - q
        slope = float(grad @ direction)
        if slope <= _TOLERANCE:
            break
        t = 1.0
        while t > 1e-10:
            candidate = q + t * direction
            value = model.log_likelihood(candidate)
            if value >= log_likelihood + 1e-4 * t * slope:
                break
            t /= 2
        else:
            break
        q = np.maximum(candidate, 0.0)
        q /= q.sum()
        if value - log_likelihood < _TOLERANCE:
            break
    return q, model.log_likelihood(q), iterations


def _blocks(n: int, block_size: int) -> list[np.ndarray]:
    return [np.arange(i, min(i + block_size, n)) for i in range(0, n, block_size)]


def project(
    p: np.ndarray,
    likelihoods: np.ndarray,
    *,
    bootstrap: int = 100,
    block_size: int = 500,
    seed: int = 620,
) -> Projection:
    """Proportions maximising the likelihood of ``likelihoods`` (n, 3) at the
    n sites whose component frequencies are the rows of ``p`` (n, K), in
    genome order (the bootstrap resamples blocks of ``block_size`` neighbours).
    """
    p = np.clip(np.asarray(p, dtype=np.float64), _P_FLOOR, 1 - _P_FLOOR)
    likelihoods = np.asarray(likelihoods, dtype=np.float64)
    n, k = p.shape
    if n == 0:
        raise ValueError("no sites to project")
    q, log_likelihood, iterations = _maximise(p, likelihoods, np.full(k, 1.0 / k), None)
    lower = upper = q
    if bootstrap:
        blocks = _blocks(n, block_size)
        rng = np.random.default_rng(seed)
        draws = np.empty((bootstrap, k))
        start = np.clip(q, 1e-3, None)
        start = start / start.sum()
        for b in range(bootstrap):
            picked = rng.integers(0, len(blocks), len(blocks))
            weights = np.bincount(
                np.concatenate([blocks[i] for i in picked]), minlength=n
            ).astype(np.float64)
            draws[b], _, _ = _maximise(p, likelihoods, start, weights)
        lower, upper = np.percentile(draws, [2.5, 97.5], axis=0)
        lower = np.minimum(lower, q)
        upper = np.maximum(upper, q)
    return Projection(q, lower, upper, n, log_likelihood, iterations)
