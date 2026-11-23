"""Check the PGS reference against pgscatalog.calc (issue #617).

``phase1`` checks that the reference sums are Phase 1's: a few panel
samples are exported as single-sample VCFs (with the indels and deletions
overlapping the scored SNVs, and without non-SNV records they don't carry, as
a caller would write them) and scored by `score pgs`'s own scorer.

    PYTHONPATH=src pixi run -m scripts/pgs_reference/pixi.toml python \
        scripts/pgs_reference/validate.py phase1 --work DIR --scores DIR [--sample ID ...]

Then two steps, in two environments (pgscatalog.calc pins NumPy 1.x):

    PYTHONPATH=src pixi run -m scripts/pgs_reference/pixi.toml python \
        scripts/pgs_reference/validate.py ours --work DIR [--target NAME=RESULT.json ...]
    pixi run -m scripts/pgs_reference/pixi.toml -e validate python \
        scripts/pgs_reference/validate.py theirs --work DIR

``ours`` applies `score pgs`'s adjustment (core/pgs_reference.py) to every
panel sample outside the training set and to the targets (the JSON results of
`score pgs` runs), and writes DIR/validate/ours.tsv. ``theirs`` gives
pgscatalog.calc 0.3.1's ``compare_ancestry`` (Mahalanobis, and its default
RandomForest) and ``pgs_adjust`` (empirical, mean, mean+var) the same
reference and targets, and reports the differences and how well calibrated
the out-of-training samples are.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NAME = "wgsextract-pgs-hgdp1kg-v2.npz"


def _built(work: Path, scores: dict) -> list[str]:
    """The scores built in ``work``: with ``package --base``, the others were
    copied from the base reference and are validated with it."""
    return [i for i in scores if (work / "scores" / f"{i}.all.npy").is_file()]


def _samples(work: Path) -> list[dict[str, str]]:
    with (work / "pcs" / "samples.tsv").open() as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _info(work: Path) -> dict:
    with np.load(work / NAME) as data:
        return json.loads(str(data["info"]))


def ours(work: Path, targets: list[str]) -> None:
    sys.path.insert(0, str(HERE.parents[1] / "src"))
    from wgsextract_cli.core import pgs_reference

    reference = pgs_reference.PgsReference(work / NAME)
    regions = np.array(
        [
            s["region"]
            for s in sorted(_samples(work), key=lambda s: int(s["training_row"]))
            if int(s["training_row"]) >= 0
        ]
    )
    rows = []
    samples = _samples(work)
    for pgs_id in _built(work, reference.info["scores"]):
        scores = np.load(work / "scores" / f"{pgs_id}.all.npy")
        for s, value in zip(samples, scores, strict=True):
            if int(s["training_row"]) >= 0:
                continue
            pcs = np.array([float(s[f"PC{i}"]) for i in range(1, 11)])
            a = pgs_reference.similarity(reference, pcs)
            a.update(pgs_reference.adjust(reference, pgs_id, float(value), pcs, regions,
                                          a["most_similar_population"]))  # fmt: skip
            rows.append({"sample": s["IID"], "panel_superpop": s["panel_superpop"], "pgs_id": pgs_id,
                         "score": float(value), **{f"PC{i}": pcs[i - 1] for i in range(1, 11)}, **a})  # fmt: skip
    for target in targets:
        name, path = target.split("=", 1)
        data = json.loads(Path(path).read_text())
        a = data["ancestry_calibration"]
        pcs = np.array(a["pcs"])
        adjusted = pgs_reference.similarity(reference, pcs)
        adjusted.update(pgs_reference.adjust(reference, data["pgs_id"], a["calibrated_score"], pcs,
                                             regions, adjusted["most_similar_population"]))  # fmt: skip
        rows.append({"sample": name, "panel_superpop": "target", "pgs_id": data["pgs_id"],
                     "score": a["calibrated_score"], **{f"PC{i}": pcs[i - 1] for i in range(1, 11)},
                     **adjusted})  # fmt: skip
    out = work / "validate"
    out.mkdir(exist_ok=True)
    with (out / "ours.tsv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {out / 'ours.tsv'}")


def phase1(work: Path, scores: Path, samples: list[str]) -> None:
    import gzip
    import subprocess
    import tempfile

    sys.path.insert(0, str(HERE.parents[1] / "src"))
    from wgsextract_cli.core import pgs, pgs_reference

    reference = pgs_reference.PgsReference(work / NAME)
    order = [s["IID"] for s in _samples(work)]
    with (work / "scores" / "panel.afreq").open() as f:
        freq = {
            r["ID"]: float(r["ALT_FREQS"]) for r in csv.DictReader(f, delimiter="\t")
        }
    out = {}
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        for s in samples:
            keep = Path(tmp) / "keep.txt"
            keep.write_text(f"{s}\n")
            parts = []
            for name in ("subset", "overlaps"):
                subprocess.run(["plink2", "--pfile", str(work / name / "panel"), "--keep", str(keep),
                                "--export", "vcf", "bgz", "--out", f"{tmp}/{s}.{name}",
                                "--threads", "2", "--memory", "3000"], check=True, capture_output=True)  # fmt: skip
                parts.append(Path(f"{tmp}/{s}.{name}.vcf.gz"))
            head, body = [], set()
            for part in parts:
                for line in gzip.open(part, "rt"):
                    if line.startswith("#"):
                        if part == parts[0]:
                            head.append(line)
                        continue
                    fields = line.split("\t")
                    fields[7] = "."  # INFO, unused and partly undeclared
                    body.add("\t".join(fields))
            rows = sorted(
                body, key=lambda x: (int(x.split("\t")[0]), int(x.split("\t")[1]))
            )
            Path(f"{tmp}/{s}.vcf").write_text("".join(head) + "".join(rows))
            subprocess.run(["bcftools", "view", "-e", 'TYPE!="snp" && (GT="RR" || GT="mis")',
                            "-Oz", "-o", f"{tmp}/{s}.vcf.gz", f"{tmp}/{s}.vcf"], check=True)  # fmt: skip
        for pgs_id in _built(work, reference.info["scores"]):
            with (work / "scores" / f"{pgs_id}.rows.tsv").open() as f:
                rows = list(csv.DictReader(f, delimiter="\t"))
            # The reference's missing genotypes are imputed with panel-wide
            # frequencies (plink2 --read-freq); so are these.
            panel_f = np.array(
                [
                    freq[r["id"]] if r["effect_is_alt"] == "1" else 1 - freq[r["id"]]
                    for r in rows
                ]
            )
            sums = np.load(work / "scores" / f"{pgs_id}.all.npy")
            scoring = pgs.ScoringFile(scores / f"{pgs_id}_GRCh38.txt.gz", "GRCh38")
            for s in samples:
                genotypes = pgs.Genotypes(
                    Path(f"{tmp}/{s}.vcf.gz"),
                    Path(tmp) / f"{s}.{pgs_id}.db",
                    None,
                    "GRCh38",
                )
                acc = reference.accumulator(pgs_id)
                pgs.score(scoring, genotypes, calibration=acc)
                genotypes.close()
                missing = np.isnan(acc.dosage)
                total = float(acc.weight @ np.where(missing, 2 * panel_f, acc.dosage))
                built = float(sums[order.index(s)])
                out[f"{pgs_id} {s}"] = {"phase1": total, "reference": built, "abs_diff": abs(total - built),
                                        "unmatched": int(missing.sum())}  # fmt: skip
                print(pgs_id, s, out[f"{pgs_id} {s}"], flush=True)
    print("max abs diff", max(v["abs_diff"] for v in out.values()))
    (work / "validate").mkdir(exist_ok=True)
    (work / "validate" / "phase1.json").write_text(json.dumps(out, indent=1) + "\n")


def theirs(work: Path) -> None:
    import pandas as pd
    from pgscatalog.calc.lib._ancestry.tools import compare_ancestry, pgs_adjust

    info = _info(work)
    samples = pd.DataFrame(_samples(work))
    for i in range(1, 11):
        samples[f"PC{i}"] = samples[f"PC{i}"].astype(float)
    samples["Unrelated"] = samples["training_row"].astype(int) >= 0
    ref = samples[samples["Unrelated"]].copy().set_index("IID")
    ours_df = pd.read_csv(work / "validate" / "ours.tsv", sep="\t")
    report: dict = {}
    for pgs_id in _built(work, info["scores"]):
        scores = np.load(work / "scores" / f"{pgs_id}.all.npy")
        all_scores = pd.Series(scores, index=samples["IID"])
        ref["score"] = all_scores.loc[ref.index].values
        target = ours_df[ours_df["pgs_id"] == pgs_id].copy().set_index("sample")
        result = {}
        for method in ("Mahalanobis", "RandomForest"):
            _, assigned, _ = compare_ancestry(
                ref_df=ref, ref_pop_col="region", target_df=target, ref_train_col="Unrelated",
                n_pcs=info["n_popcomp"], method=method,
                p_threshold=1e-10 if method == "Mahalanobis" else 0.5,
            )  # fmt: skip
            target[f"pop_{method}"] = assigned["MostSimilarPop"]
            result[f"most_similar_agreement_{method}"] = float(
                (assigned["MostSimilarPop"] == target["most_similar_population"]).mean()
            )
        target["MostSimilarPop"] = target["pop_Mahalanobis"]
        _, adjusted, models = pgs_adjust(
            ref_df=ref, target_df=target, scorecols=["score"], ref_pop_col="region",
            target_pop_col="MostSimilarPop", use_method=["empirical", "mean", "mean+var"],
            ref_train_col="Unrelated", n_pcs=info["n_normalization"],
        )  # fmt: skip
        for ours_col, their_col in (
            ("z_norm1", "Z_norm1|score"),
            ("z_norm2", "Z_norm2|score"),
            ("empirical_z", "Z_MostSimilarPop|score"),
            ("empirical_percentile", "percentile_MostSimilarPop|score"),
        ):
            diff = (target[ours_col] - adjusted[their_col]).abs()
            result[f"max_abs_diff_{ours_col}"] = float(diff.max())
        targets = target[target["panel_superpop"] == "target"]
        result["targets"] = {
            name: {
                "ours": {
                    k: float(targets.loc[name, k])
                    for k in (
                        "z_norm2",
                        "adjusted_percentile",
                        "empirical_percentile",
                        "z_norm1",
                    )
                },
                "pgscatalog_calc": {
                    "Z_norm2": float(adjusted.loc[name, "Z_norm2|score"]),
                    "percentile_from_Z_norm2": 50
                    * (
                        1
                        + math.erf(
                            float(adjusted.loc[name, "Z_norm2|score"]) / math.sqrt(2)
                        )
                    ),
                    "percentile_MostSimilarPop": float(
                        adjusted.loc[name, "percentile_MostSimilarPop|score"]
                    ),
                    "Z_norm1": float(adjusted.loc[name, "Z_norm1|score"]),
                    "MostSimilarPop_RandomForest": str(
                        targets.loc[name, "pop_RandomForest"]
                    ),
                },
            }
            for name in targets.index
        }
        panel = target[target["panel_superpop"] != "target"]
        result["out_of_training"] = {
            pop: {
                "n": int(len(g)),
                "z_norm2_mean": float(g["z_norm2"].mean()),
                "z_norm2_sd": float(g["z_norm2"].std()),
                "share_within_95": float((g["z_norm2"].abs() < 1.96).mean()),
            }
            for pop, g in panel.groupby("panel_superpop")
        }
        result["reference_similarity_p_below_1e-10"] = float(
            (panel["reference_similarity_p"] < 1e-10).mean()
        )
        result["norm2_fit"] = models["adjust_pcs"]["PGS"]["score"]["Z_norm2"]["params"][
            "success"
        ]
        report[pgs_id] = result
        print(
            pgs_id,
            json.dumps(
                {
                    k: v
                    for k, v in result.items()
                    if k not in ("targets", "out_of_training")
                }
            ),
        )
    (work / "validate" / "report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=("phase1", "ours", "theirs"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--scores", type=Path, help="phase1: folder of {PGS}_GRCh38.txt.gz"
    )
    parser.add_argument(
        "--sample", action="append", default=[], help="phase1: panel sample ID"
    )
    parser.add_argument(
        "--target", action="append", default=[], help="NAME=score pgs result JSON"
    )
    args = parser.parse_args()
    if args.step == "phase1":
        phase1(
            args.work, args.scores, args.sample or ["HG00096", "NA19240", "HGDP00557"]
        )
    elif args.step == "ours":
        ours(args.work, args.target)
    else:
        theirs(args.work)


if __name__ == "__main__":
    main()
