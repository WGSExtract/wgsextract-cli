"""``build.py package``: fit pgscatalog.calc's ancestry adjustments on the
reference's training samples and write the reference file.

The methods and their defaults are pgscatalog.calc 0.3.1's
(``pgscatalog/calc/lib/_ancestry/tools.py``, Apache-2.0), reimplemented with
NumPy/SciPy so `score pgs` needs neither pandas nor scikit-learn:

- most similar population: Mahalanobis distance (empirical covariance) to
  each reference region on PCs 1-5, p = chi2.sf(d^2, 5 - 1)
  (``compare_ancestry(method="Mahalanobis")``);
- ``empirical``: the score's percentile (``percentileofscore``, kind "rank")
  and Z among the training samples of that region;
- ``mean`` (Khera et al. 2019): Z_norm1, the residual of a linear regression
  of the centred score on PCs 1-4 (each standardised over the training
  samples), divided by the residuals' SD;
- ``mean+var`` (Khan et al. 2022; eMERGE GIRA): Z_norm2, the score's
  deviation from a mean that is linear in the PCs, divided by the SD of a
  variance that is log-linear in them, both fitted jointly by maximum
  likelihood (BFGS, started from the two-step fit).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from build import BUILD, N_NORMALIZATION, N_POPCOMP, NAME, VERSION, _read_sscore, pgs
from scipy import optimize


def _samples(work: Path) -> tuple[list[str], np.ndarray]:
    with (work / "pcs" / "samples.tsv").open() as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    return [r["IID"] for r in rows], np.array([int(r["training_row"]) for r in rows])


def _nll(theta: np.ndarray, x: np.ndarray, y: np.ndarray) -> float:
    k = x.shape[1] + 1
    mu = theta[0] + x @ theta[1:k]
    log_var = theta[k] + x @ theta[k + 1 :]
    return float(np.sum(0.5 * log_var + 0.5 * (y - mu) ** 2 / np.exp(log_var)))


def _grad(theta: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    k = x.shape[1] + 1
    mu = theta[0] + x @ theta[1:k]
    var = np.exp(theta[k] + x @ theta[k + 1 :])
    r = y - mu
    d_mu = -r / var
    d_logvar = 0.5 - 0.5 * r**2 / var
    design = np.column_stack([np.ones(len(y)), x])
    return np.concatenate([design.T @ d_mu, design.T @ d_logvar])


def _gamma_log_glm(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Unpenalised Gamma GLM with log link (IRLS), the second step's start."""
    design = np.column_stack([np.ones(len(y)), x])
    beta = np.zeros(design.shape[1])
    beta[0] = np.log(y.mean())
    for _ in range(100):
        mu = np.exp(design @ beta)
        z = design @ beta + (y - mu) / mu
        new = np.linalg.lstsq(design, z, rcond=None)[0]
        if np.abs(new - beta).max() < 1e-10:
            return new
        beta = new
    return beta


def fit(pcs: np.ndarray, region: np.ndarray, scores: np.ndarray) -> dict:
    """The adjustment models of one score on the training samples."""
    model: dict = {"regions": {}}
    for name in sorted(set(region)):
        s = scores[region == name]
        model["regions"][name] = {
            "n": int(s.size),
            "mean": float(s.mean()),
            "std": float(s.std()),
        }
    x = pcs[:, :N_NORMALIZATION]
    pc_mean, pc_std = x.mean(axis=0), x.std(axis=0)
    xn = (x - pc_mean) / pc_std
    offset = float(scores.mean())
    y = scores - offset
    design = np.column_stack([np.ones(len(y)), xn])
    beta_mu = np.linalg.lstsq(design, y, rcond=None)[0]
    resid = y - design @ beta_mu
    beta_var = _gamma_log_glm(xn, (resid - resid.mean()) ** 2)
    start = np.concatenate([beta_mu, beta_var])
    fitted = optimize.minimize(_nll, start, args=(xn, y), jac=_grad, method="BFGS",
                               options={"maxiter": 1000})  # fmt: skip
    k = N_NORMALIZATION + 1
    model.update(
        pc_mean=pc_mean.tolist(),
        pc_std=pc_std.tolist(),
        pgs_offset=offset,
        norm1_coef=beta_mu.tolist(),
        norm1_resid_std=float(resid.std()),
        norm2_mu_coef=fitted.x[:k].tolist(),
        norm2_var_coef=fitted.x[k:].tolist(),
        norm2_converged=bool(fitted.success),
        norm2_message=str(fitted.message),
    )
    return model


def _populations(pcs: np.ndarray, region: np.ndarray) -> dict:
    """Mean and precision (inverse empirical covariance) on PCs 1-5, per
    region and over all training samples."""
    x = pcs[:, :N_POPCOMP]

    def one(rows: np.ndarray) -> dict:
        centred = x[rows] - x[rows].mean(axis=0)
        covariance = centred.T @ centred / rows.sum()
        return {
            "n": int(rows.sum()),
            "location": x[rows].mean(axis=0).tolist(),
            "precision": np.linalg.pinv(covariance).tolist(),
        }

    out = {"ALL": one(np.ones(len(region), dtype=bool))}
    out.update({name: one(region == name) for name in sorted(set(region))})
    return out


def package(work: Path, ancestry: Path, ids: list[str], base: Path | None) -> None:
    """The reference file for ``ids``; with ``base``, a published reference,
    every other curated score is copied from it unchanged (a rebuilt score
    that ``base`` also has is compared with it), and scores no longer curated
    are dropped."""
    ref = np.load(ancestry)
    ancestry_sha = hashlib.sha256(ancestry.read_bytes()).hexdigest()
    samples, training = _samples(work)
    panel_samples, sums = _read_sscore(work / "scores" / "panel.sscore")
    assert panel_samples == samples, "sample order differs between steps"
    summary = json.loads((work / "scores" / "summary.json").read_text())

    def alt_frequencies(path: Path) -> dict[str, float]:
        with path.open() as f:
            return {
                r["ID"]: float(r["ALT_FREQS"])
                for r in csv.DictReader(f, delimiter="\t")
            }

    regions = sorted(
        p.stem.removeprefix("region_") for p in (work / "scores").glob("region_*.afreq")
    )
    freqs = [alt_frequencies(work / "scores" / f"region_{r}.afreq") for r in regions]
    overall = alt_frequencies(work / "scores" / "panel.afreq")
    train = np.flatnonzero(training >= 0)
    order = train[np.argsort(training[train])]  # panel rows in training order
    rows_used = training[order]  # their rows in the ancestry reference's PCA
    pcs = ref["pca_scores"].astype(np.float64)[rows_used]
    region = ref["pca_region"].astype(str)[rows_used]
    arrays: dict[str, np.ndarray] = {"training_rows": rows_used.astype(np.int32)}
    scores_info = {}
    for pgs_id in ids:
        total = (
            summary[pgs_id]["offset"]
            + sums[f"{pgs_id}_SUM"]
            + np.load(work / "scores" / f"{pgs_id}.overlap_correction.npy")
        )
        with (work / "scores" / f"{pgs_id}.rows.tsv").open() as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        mask = np.zeros(summary[pgs_id]["rows"], dtype=bool)
        index = np.array([int(r["row"]) for r in rows], dtype=np.int64)
        mask[index] = True
        alt = np.array([[f[r["id"]] for f in freqs] for r in rows])
        # A region with no calls at a site takes the panel-wide frequency.
        panel_alt = np.array([overall[r["id"]] for r in rows])[:, None]
        alt = np.where(np.isnan(alt), panel_alt, alt)
        is_alt = np.array([r["effect_is_alt"] == "1" for r in rows])[:, None]
        effect_freq = np.where(is_alt, alt, 1 - alt)  # rows x regions
        arrays[f"{pgs_id}_rows"] = np.packbits(mask)
        arrays[f"{pgs_id}_frequency"] = np.round(effect_freq * 65535).astype(np.uint16)
        arrays[f"{pgs_id}_scores"] = total[order]
        model = fit(pcs, region, total[order])
        scores_info[pgs_id] = {
            "scoring_file_sha256": summary[pgs_id]["sha256"],
            "rows": summary[pgs_id]["rows"],
            "calibration_rows": int(mask.sum()),
            "reasons": summary[pgs_id]["reasons"],
            "model": model,
        }
        print(pgs_id, scores_info[pgs_id]["calibration_rows"], "rows;",
              {k: model[k] for k in ("norm1_resid_std", "norm2_converged")})  # fmt: skip
        # All panel samples, for validation (validate.py).
        np.save(work / "scores" / f"{pgs_id}.all.npy", total)
    info = {
        "version": VERSION,
        "build": BUILD,
        "panel": "pgsc_calc HGDP+1kGP reference v1 (gnomAD v3.1 HGDP+1KG; CC0 1.0)",
        "ancestry_reference_sha256": ancestry_sha,
        "training_samples": len(order),
        "n_popcomp": N_POPCOMP,
        "n_normalization": N_NORMALIZATION,
        "method": "pgscatalog.calc 0.3.1 ancestry adjustment (empirical, mean, mean+var full likelihood; Mahalanobis population similarity)",
        "frequency_regions": regions,
        "frequency_encoding": "effect-allele frequency x 65535 (uint16) in each region of frequency_regions, for the rows set in <id>_rows (np.packbits, row order of the scoring file)",
        "populations": _populations(pcs, region),
        "scores": scores_info,
    }
    if base is not None:
        with np.load(base) as data:
            kept = {name: data[name] for name in data.files}
        base_info = json.loads(str(kept.pop("info")))
        if base_info["ancestry_reference_sha256"] != ancestry_sha or not np.array_equal(
            kept["training_rows"], arrays["training_rows"]
        ):
            raise SystemExit(f"{base} was built on other training samples")
        for pgs_id in set(ids) & set(base_info["scores"]):
            diff = np.abs(arrays[f"{pgs_id}_scores"] - kept[f"{pgs_id}_scores"])
            print(f"{pgs_id} rebuilt: max |score - {base.name}'s| {diff.max():.3g}")
        for pgs_id in pgs.CURATED.values():
            if pgs_id not in ids:
                scores_info[pgs_id] = base_info["scores"][pgs_id]
                for part in ("rows", "frequency", "scores"):
                    arrays[f"{pgs_id}_{part}"] = kept[f"{pgs_id}_{part}"]
        order = [
            *pgs.CURATED.values(),
            *(i for i in ids if i not in pgs.CURATED.values()),
        ]
        info["scores"] = {pgs_id: scores_info[pgs_id] for pgs_id in order}
        info["rebuilt_from"] = {
            "name": base.name,
            "sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
            "scores": ids,
        }
    arrays["info"] = np.array(json.dumps(info))
    out = work / NAME
    np.savez_compressed(out, **arrays)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{out} {out.stat().st_size / 1e6:.1f} MB sha256 {digest}")
