"""Build the `score pgs` reference distributions (issue #617, Phase 2).

For each curated score, the HGDP+1KG reference samples are scored with Phase
1's variant rules, projected onto the `qc ancestry` PCA (ancestry-ref-1), and
pgscatalog.calc's ancestry adjustments are fitted on them. The result is one
file, ``wgsextract-pgs-hgdp1kg-v2.npz``, published as a GitHub release asset
(``pgs-ref-2``) and downloaded by `score pgs` on first use. Version 1
(``pgs-ref-1``) had PGS000027 for BMI; version 2 replaces it with PGS005198
(issue #1165) and copies the other ten scores from version 1 unchanged.

Run each step in order with this folder's Pixi environment (plink2, NumPy,
SciPy) and the CLI's sources on PYTHONPATH (for its scoring-file reader):

    PYTHONPATH=src pixi run -m scripts/pgs_reference/pixi.toml python \
        scripts/pgs_reference/build.py STEP --work DIR [options]

Steps:

- ``subset``: the panel's records at the ancestry sites and at the scores'
  positions, as a small pfile (plink2 can't hold all 84M variants' IDs in a
  modest workspace).
- ``training`` (instead of ``training_ids.py``, when rebuilding scores in a
  published reference with ``--base``): the training samples' IDs, by
  matching each panel sample's projection to the in-sample PCs it reproduces.
- ``pcs``: the reference panel's samples projected onto the ancestry
  reference's 10 PCs (plink2 ``--score variance-standardize`` with the PCA's
  own allele frequencies, the projection `score pgs` applies to a user). The
  3,129 samples the PCA was trained on (by ID, from ``training_ids.py``),
  those of them in the panel, are the training set, as the unrelated samples
  are in pgscatalog.calc; their projections are checked against their
  in-sample scores.
- ``score``: each scoring file's rows matched to the panel with Phase 1's
  rules (autosomal SNV, not A/T or C/G, effect and other allele exactly the
  REF and ALT of the panel's only SNV record at the position), then all
  samples scored in one plink2 run. Missing reference genotypes are
  mean-imputed with the panel's frequency (plink2 ``--read-freq``, as
  pgsc_calc scores its reference).
- ``overlaps``: where a sample also carries an indel starting at, or a
  deletion spanning, a scored SNV, Phase 1 would leave that SNV unmatched;
  its contribution is swapped for the mean imputation `score pgs` applies.
- ``package``: pgscatalog.calc 0.3.1's adjustments fitted on the training
  samples (empirical distribution of the most similar region; Khera et al.
  2019 mean regression on PCs; Khan et al. 2022 / eMERGE GIRA mean+variance
  full-likelihood fit), and the reference file written with its SHA-256.
  With ``--base`` (a published reference), only ``--ids`` are rebuilt; the
  other curated scores are copied from it.

Version 2 was built on macOS (osx-arm64) with ``--ids PGS000297 PGS005198``
up to ``overlaps`` (PGS000297 checks the rebuild against version 1), then
``package --ids PGS005198 --base wgsextract-pgs-hgdp1kg-v1.npz``.

Data: pgsc_calc's HGDP+1kGP reference panel (pgsc_HGDP+1kGP_v1, from gnomAD
v3.1 HGDP+1KG, CC0 1.0); the scoring files are not redistributed, only the
rows of each that the reference uses (as a bit mask over the rows of the
pinned file) and their reference frequencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from wgsextract_cli.core import pgs  # noqa: E402

VERSION = 2
NAME = f"wgsextract-pgs-hgdp1kg-v{VERSION}.npz"
PANEL = "GRCh38_HGDP+1kGP_ALL"
BUILD = "GRCh38"
# pgscatalog.calc ancestry_cli defaults: PCs for population comparison and
# for normalisation.
N_POPCOMP = 5
N_NORMALIZATION = 4
PALINDROMES = {"AT", "TA", "CG", "GC"}


def _run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def _plink(work: Path, panel: Path, *args: str) -> None:
    """plink2 on the subset of the panel at the sites the reference uses."""
    _run(
        "plink2", "--pfile", str(work / "subset" / "panel"),
        "--set-all-var-ids", "@:#:$r:$a", "--new-id-max-allele-len", "1000",
        "--threads", str(_threads), "--memory", str(_memory),
        *args,
    )  # fmt: skip


def _read_sscore(path: Path) -> tuple[list[str], dict[str, np.ndarray]]:
    with path.open() as f:
        rows = list(csv.reader(f, delimiter="\t"))
    header = rows[0]
    iid = header.index("IID") if "IID" in header else header.index("#IID")
    samples = [r[iid] for r in rows[1:]]
    columns = {
        name: np.array([float(r[i]) for r in rows[1:]])
        for i, name in enumerate(header)
        if name.endswith("_SUM") and name != "NAMED_ALLELE_DOSAGE_SUM"
    }
    return samples, columns


def subset(
    work: Path,
    panel: Path,
    ancestry: Path,
    scores: Path,
    ids: list[str],
    chrom: str | None,
) -> None:
    """The panel's records at the ancestry sites and the scores' positions
    (the full panel's 84M variants don't fit plink2's default workspace)."""
    ref = np.load(ancestry)
    positions = {
        (str(c), int(p))
        for c, p in zip(ref["chrom_GRCh38"], ref["pos_GRCh38"], strict=True)
    }
    for pgs_id in ids:
        for v in pgs.ScoringFile(scores / f"{pgs_id}_{BUILD}.txt.gz", BUILD).variants():
            if v.supported:
                positions.add((v.chrom, v.pos))
    out = work / "subset"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "sites.bed").open("w") as f:
        f.writelines(f"{c}\t{p - 1}\t{p}\n" for c, p in sorted(positions, key=lambda x: (int(x[0]), x[1]))
                     if chrom in (None, c))  # fmt: skip
    _run("plink2", "--pfile", str(panel / PANEL), "vzs", "--extract", "bed0",
         str(out / "sites.bed"), "--threads", str(_threads), "--memory", str(_memory),
         "--make-pgen", "--out", str(out / "panel"))  # fmt: skip


def _project(work: Path, panel: Path, ancestry: Path) -> tuple[list[str], np.ndarray]:
    """Every panel sample projected onto the ancestry reference's PCs."""
    ref = np.load(ancestry)
    chrom, pos = ref["chrom_GRCh38"], ref["pos_GRCh38"]
    alleles = [
        (r.decode(), a.decode()) for r, a in zip(ref["ref"], ref["alt"], strict=True)
    ]
    ids = [f"{c}:{p}:{r}:{a}" for c, p, (r, a) in zip(chrom, pos, alleles, strict=True)]
    freq, loadings = ref["pca_frequency"].astype(np.float64), ref["pca_loadings"]
    out = work / "pcs"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "loadings.tsv").open("w") as f:
        f.write(
            "ID\tA1\t"
            + "\t".join(f"PC{i + 1}" for i in range(loadings.shape[1]))
            + "\n"
        )
        for vid, (_, alt), row in zip(ids, alleles, loadings, strict=True):
            f.write(f"{vid}\t{alt}\t" + "\t".join(f"{x:.9g}" for x in row) + "\n")
    with (out / "pca.afreq").open("w") as f:
        f.write("#CHROM\tID\tREF\tALT\tALT_FREQS\tOBS_CT\n")
        for c, vid, (r, a), p in zip(chrom, ids, alleles, freq, strict=True):
            f.write(f"{c}\t{vid}\t{r}\t{a}\t{p:.9g}\t6258\n")
    k = loadings.shape[1]
    _plink(work, panel, "--extract", str(out / "loadings.tsv"),
           "--read-freq", str(out / "pca.afreq"),
           "--score", str(out / "loadings.tsv"), "1", "2", "header-read",
           "variance-standardize", "cols=+scoresums",
           "--score-col-nums", f"3-{k + 2}", "--out", str(out / "panel"))  # fmt: skip
    samples, columns = _read_sscore(out / "panel.sscore")
    return samples, np.column_stack([columns[f"PC{i + 1}_SUM"] for i in range(k)])


def training(work: Path, panel: Path, ancestry: Path, base: Path) -> None:
    """``training.tsv`` without training_ids.py (which needs Hail and gnomAD's
    sample tables): each panel sample is matched to the training sample whose
    in-sample PCs its projection reproduces. Only mutual nearest neighbours
    that no other sample comes close to are accepted, and the matched rows must be exactly the training samples of
    the published reference ``base``, which stores their rows but not IDs."""
    ref = np.load(ancestry)
    samples, projected = _project(work, panel, ancestry)
    trained = ref["pca_scores"].astype(np.float64)
    scale = float(np.sqrt((trained**2).sum(1)).mean())
    distance = np.sqrt(np.maximum(0.0, (projected**2).sum(1)[:, None]
                                  + (trained**2).sum(1)[None, :]
                                  - 2 * projected @ trained.T))  # fmt: skip
    nearest = distance.argmin(1)
    gap = distance[np.arange(len(samples)), nearest]
    second = np.partition(distance, 1, axis=1)[:, 1]
    # Mutual nearest neighbours, each far closer to the other than to anyone
    # else (relatives of training samples are near in PC space, not on them).
    mutual = distance.argmin(0)[nearest] == np.arange(len(samples))
    matched = mutual & (gap < 0.1 * second)
    rows = nearest[matched]
    with np.load(base) as data:
        expected = set(data["training_rows"].tolist())
    print(f"{int(matched.sum())} of {len(samples)} panel samples match a training "
          f"sample: distance median {np.median(gap[matched]):.3g}, max "
          f"{gap[matched].max():.3g}; next best min {second[matched].min():.3g} "
          f"(typical |PC| {scale:.3g}); expected {len(expected)}")  # fmt: skip
    if set(rows.tolist()) != expected:
        raise SystemExit(
            "the matched training samples are not the base reference's training "
            f"set: {len(expected - set(rows.tolist()))} missing, "
            f"{len(set(rows.tolist()) - expected)} extra"
        )
    with (work / "training.tsv").open("w") as f:
        f.write("training_row\tIID\tpopulation\tregion\n")
        for s, t in sorted(zip(np.array(samples)[matched], rows, strict=True),
                           key=lambda x: x[1]):  # fmt: skip
            f.write(f"{t}\t{s}\t{ref['pca_population'][t]}\t{ref['pca_region'][t]}\n")


def pcs(work: Path, panel: Path, ancestry: Path, training: Path) -> None:
    ref = np.load(ancestry)
    samples, projected = _project(work, panel, ancestry)
    k = projected.shape[1]
    trained = ref["pca_scores"].astype(np.float64)
    # The training samples by ID (training_ids.py); their projection here is
    # checked against their in-sample PCA scores.
    with training.open() as f:
        rows = {
            r["IID"]: int(r["training_row"]) for r in csv.DictReader(f, delimiter="\t")
        }
    index = np.array([rows.get(s, -1) for s in samples])
    present = index >= 0
    gap = np.sqrt(((projected[present] - trained[index[present]]) ** 2).sum(1))
    scale = float(np.sqrt((trained**2).sum(1)).mean())
    missing = sorted(set(range(len(trained))) - set(index[present]))
    regions, counts = np.unique(ref["pca_region"][missing], return_counts=True)
    print(f"panel samples {len(samples)}; training samples {len(trained)}, "
          f"{int(present.sum())} in the panel; projection vs in-sample PCs: median "
          f"distance {np.median(gap):.3g}, 99th percentile {np.percentile(gap, 99):.3g}, "
          f"max {gap.max():.3g} (typical |PC| {scale:.3g}); not in the panel by region "
          f"{dict(zip(regions.tolist(), counts.tolist(), strict=True))}")  # fmt: skip
    training_row = index
    # The ancestry reference's held-out samples (training_ids.py's sets/),
    # for validation.
    holdout_file = training.parent / "sets" / "holdout.txt"
    holdout = (
        set(holdout_file.read_text().split()[1::2]) if holdout_file.is_file() else set()
    )
    psam = _psam(panel)
    with (work / "pcs" / "samples.tsv").open("w") as f:
        f.write("IID\ttraining_row\theld_out\tregion\tpopulation\tpanel_superpop\tpanel_population\t"
                + "\t".join(f"PC{i + 1}" for i in range(k)) + "\n")  # fmt: skip
        for i, s in enumerate(samples):
            t = training_row[i]
            region = str(ref["pca_region"][t]) if t >= 0 else ""
            population = str(ref["pca_population"][t]) if t >= 0 else ""
            meta = psam.get(s, {})
            f.write(f"{s}\t{t}\t{int(s in holdout)}\t{region}\t{population}\t{meta.get('SuperPop', '')}\t"
                    f"{meta.get('Population', '')}\t"
                    + "\t".join(f"{x:.9g}" for x in projected[i]) + "\n")  # fmt: skip


def _psam(panel: Path) -> dict[str, dict[str, str]]:
    with (panel / f"{PANEL}.psam").open() as f:
        rows = list(csv.reader(f, delimiter="\t"))
    header = [h.lstrip("#") for h in rows[0]]
    return {
        r[header.index("IID")]: dict(zip(header, r, strict=False)) for r in rows[1:]
    }


def _panel_snvs(
    work: Path, wanted: set[tuple[str, int]]
) -> dict[tuple[str, int], list[tuple[str, str, str]]]:
    """The panel's SNV records (REF, ALT, FILTER) at the wanted positions."""
    found: dict[tuple[str, int], list[tuple[str, str, str]]] = {}
    with (work / "subset" / "panel.pvar").open() as pvar:
        for line in pvar:
            if line.startswith("#"):
                continue
            chrom, pos, _, ref, alt, filt = line.split("\t", 7)[:6]
            key = (chrom.removeprefix("chr"), int(pos))
            if key in wanted and len(ref) == 1 and len(alt) == 1:
                found.setdefault(key, []).append((ref, alt, filt))
    return found


def _match(variant: pgs.Variant, records: list[tuple[str, str, str]] | None) -> str:
    """Phase 1's variant rules, with the panel's records as the call set: one
    SNV record, passing its filters, of exactly the effect and other allele."""
    if not variant.supported:
        return "unsupported"
    if variant.effect + variant.other in PALINDROMES:
        return "strand_ambiguous"
    if not records:
        return "missing"
    if len(records) != 1:
        return "ambiguous"
    if records[0][2] not in ("PASS", "."):
        return "filtered"
    if {variant.effect, variant.other} != set(records[0][:2]):
        return "allele_mismatch"
    return "called"


def score(
    work: Path, panel: Path, scores: Path, ids: list[str], chrom: str | None
) -> None:
    """``chrom`` limits the run to one chromosome (for sizing)."""
    out = work / "scores"
    out.mkdir(parents=True, exist_ok=True)
    files = {i: scores / f"{i}_{BUILD}.txt.gz" for i in ids}
    wanted: set[tuple[str, int]] = set()
    for path in files.values():
        for v in pgs.ScoringFile(path, BUILD).variants():
            if v.supported and chrom in (None, v.chrom):
                wanted.add((v.chrom, v.pos))
    print(
        f"{len(wanted):,} scoring positions; reading the panel's variants", flush=True
    )
    snvs = _panel_snvs(work, wanted)
    # Every score in one plink2 run: weights on the ALT allele, and the flipped
    # rows' constant 2w added afterwards (w * effect dosage = 2w - w * ALT dosage).
    weights: dict[str, dict[str, float]] = {}
    offsets: dict[str, float] = {}
    summary = {}
    for pgs_id, path in files.items():
        scoring = pgs.ScoringFile(path, BUILD)
        rows, reasons = [], {}
        offset = 0.0
        for number, v in enumerate(scoring.variants()):
            reason = _match(v, snvs.get((v.chrom, v.pos)))
            reasons[reason] = reasons.get(reason, 0) + 1
            if reason != "called":
                continue
            ref, alt, _ = snvs[(v.chrom, v.pos)][0]
            vid = f"{v.chrom}:{v.pos}:{ref}:{alt}"
            sign = 1.0 if v.effect == alt else -1.0
            if sign < 0:
                offset += 2 * v.weight
            weights.setdefault(vid, {})[pgs_id] = (
                weights.get(vid, {}).get(pgs_id, 0.0) + sign * v.weight
            )
            rows.append((number, vid, v.effect == alt))
        offsets[pgs_id] = offset
        with (out / f"{pgs_id}.rows.tsv").open("w") as f:
            f.write("row\tid\teffect_is_alt\n")
            f.writelines(f"{n}\t{vid}\t{int(a)}\n" for n, vid, a in rows)
        summary[pgs_id] = {
            "sha256": scoring.provenance()["sha256"],
            "rows": sum(reasons.values()),
            "reasons": reasons,
            "offset": offset,
        }
        print(pgs_id, summary[pgs_id], flush=True)
    order = list(files)
    with (out / "weights.tsv").open("w") as f:
        f.write("ID\tA1\t" + "\t".join(order) + "\n")
        for vid, ws in weights.items():
            f.write(f"{vid}\t{vid.rsplit(':', 1)[1]}\t"
                    + "\t".join(f"{ws.get(i, 0.0):.17g}" for i in order) + "\n")  # fmt: skip
    _plink(work, panel, "--extract", str(out / "weights.tsv"), "--freq",
           "--out", str(out / "panel"))  # fmt: skip
    # Each region's frequencies among the training samples: `score pgs`
    # imputes a user's missing rows with those of the region they are most
    # similar to (panel-wide frequencies bias the imputed sum by up to 0.24 SD
    # for a non-European sample; out/617/impute_check.json).
    with (work / "pcs" / "samples.tsv").open() as f:
        training = [
            r for r in csv.DictReader(f, delimiter="\t") if int(r["training_row"]) >= 0
        ]
    for region in sorted({r["region"] for r in training}):
        keep = out / f"{region}.keep"
        keep.write_text(
            "".join(f"{r['IID']}\n" for r in training if r["region"] == region)
        )
        _plink(work, panel, "--extract", str(out / "weights.tsv"), "--keep", str(keep),
               "--freq", "--out", str(out / f"region_{region}"))  # fmt: skip
    _plink(work, panel, "--extract", str(out / "weights.tsv"),
           "--read-freq", str(out / "panel.afreq"),
           "--score", str(out / "weights.tsv"), "1", "2", "header-read",
           "cols=+scoresums", "--score-col-nums", "3" if len(order) == 1 else f"3-{len(order) + 2}",
           "--out", str(out / "panel"))  # fmt: skip
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def _raw(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    """Samples, variant IDs (chrom:pos:ref:alt) and ALT dosages (NaN when
    missing) of a plink2 ``--export A`` file."""
    with path.open() as f:
        header = f.readline().rstrip("\n").split("\t")
        samples, rows = [], []
        for line in f:
            fields = line.rstrip("\n").split("\t")
            samples.append(fields[1])
            rows.append([float(x) if x != "NA" else np.nan for x in fields[6:]])
    ids = [h.rsplit("_", 1)[0] for h in header[6:]]
    dosage = np.array(rows)
    # plink2 counts the REF allele (the column name's suffix); turn the
    # columns that do into ALT dosages.
    counts_ref = np.array(
        [
            h.rsplit("_", 1)[1] == v.split(":")[2]
            for h, v in zip(header[6:], ids, strict=True)
        ]
    )
    dosage[:, counts_ref] = 2 - dosage[:, counts_ref]
    return samples, ids, dosage


def overlaps(work: Path, panel: Path) -> None:
    """Phase 1 leaves an SNV unmatched when the sample also has an indel or
    other non-SNV call starting at it (two records: ``ambiguous``) or a
    deletion spanning it (``overlapping_nonreference``). The panel was scored
    on the SNV record alone, so for each sample that carries such a record,
    swap the SNV's contribution for its mean imputation, as `score pgs` does
    for a user's unmatched calibration rows."""
    out = work / "scores"
    rows_by_id: dict[str, list[str]] = {}
    positions: dict[str, set[int]] = {}
    for path in sorted(out.glob("*.rows.tsv")):
        with path.open() as f:
            for r in csv.DictReader(f, delimiter="\t"):
                rows_by_id.setdefault(r["id"], [])
                c, p = r["id"].split(":")[:2]
                positions.setdefault(c, set()).add(int(p))
    by_chrom = {c: np.array(sorted(v)) for c, v in positions.items()}
    pairs: list[tuple[str, str]] = []  # (overlapping record, SNV position key)
    starts: set[tuple[str, int]] = set()
    with subprocess.Popen(["zstd", "-dc", str(panel / f"{PANEL}.pvar.zst")],
                          stdout=subprocess.PIPE, text=True) as zstd:  # fmt: skip
        assert zstd.stdout is not None
        for line in zstd.stdout:
            if line.startswith("#"):
                continue
            chrom, pos, _, ref, alt = line.split("\t", 5)[:5]
            if len(ref) == 1 and len(alt) == 1:
                continue
            here = by_chrom.get(chrom)
            if here is None:
                continue
            start, end = int(pos), int(pos) + len(ref) - 1
            hits = here[
                np.searchsorted(here, start) : np.searchsorted(here, end, side="right")
            ]
            for p in hits:
                pairs.append((f"{chrom}:{start}:{ref}:{alt}", f"{chrom}:{p}"))
                starts.add((chrom, start))
    if zstd.returncode:
        raise subprocess.CalledProcessError(zstd.returncode, zstd.args)
    print(
        f"{len(pairs):,} SNV/non-SNV overlaps at {len(starts):,} record starts",
        flush=True,
    )
    work_dir = work / "overlaps"
    work_dir.mkdir(exist_ok=True)
    with (work_dir / "starts.bed").open("w") as f:
        f.writelines(
            f"{c}\t{p - 1}\t{p}\n"
            for c, p in sorted(starts, key=lambda x: (int(x[0]), x[1]))
        )
    with (work_dir / "records.txt").open("w") as f:
        f.writelines(f"{r}\n" for r in sorted({r for r, _ in pairs}))
    _run("plink2", "--pfile", str(panel / PANEL), "vzs", "--extract", "bed0",
         str(work_dir / "starts.bed"), "--threads", str(_threads), "--memory", str(_memory),
         "--make-pgen", "--out", str(work_dir / "panel"))  # fmt: skip
    _run("plink2", "--pfile", str(work_dir / "panel"), "--set-all-var-ids", "@:#:$r:$a",
         "--new-id-max-allele-len", "1000", "--extract", str(work_dir / "records.txt"),
         "--export", "A", "--out", str(work_dir / "records"),
         "--threads", str(_threads), "--memory", "4000")  # fmt: skip
    keys = {k for _, k in pairs}
    snvs = [vid for vid in rows_by_id if ":".join(vid.split(":")[:2]) in keys]
    (work_dir / "snvs.txt").write_text("".join(f"{v}\n" for v in snvs))
    _plink(work, panel, "--extract", str(work_dir / "snvs.txt"), "--export", "A",
           "--out", str(work_dir / "snvs"))  # fmt: skip
    samples, record_ids, carried = _raw(work_dir / "records.raw")
    snv_samples, snv_ids, g = _raw(work_dir / "snvs.raw")
    assert samples == snv_samples
    record_col = {v: i for i, v in enumerate(record_ids)}
    snv_by_key: dict[str, list[int]] = {}
    for i, v in enumerate(snv_ids):
        snv_by_key.setdefault(":".join(v.split(":")[:2]), []).append(i)
    blocked = np.zeros(g.shape, dtype=bool)  # sample x affected SNV
    for record, key in pairs:
        column = record_col.get(record)
        if column is None:
            continue
        carriers = np.nan_to_num(carried[:, column]) > 0
        for j in snv_by_key.get(key, []):
            blocked[:, j] |= carriers
    freq = {}
    with (out / "panel.afreq").open() as f:
        for r in csv.DictReader(f, delimiter="\t"):
            freq[r["ID"]] = float(r["ALT_FREQS"])
    wanted = set(snv_ids)
    with (out / "weights.tsv").open() as f:
        reader = csv.DictReader(f, delimiter="\t")
        ids = [c for c in reader.fieldnames or [] if c not in ("ID", "A1")]
        alt_weight = {
            r["ID"]: {i: float(r[i]) for i in ids} for r in reader if r["ID"] in wanted
        }
    f_alt = np.array([freq[v] for v in snv_ids])
    # Mean imputation minus the scored genotype (already imputed if missing).
    delta = np.where(blocked & ~np.isnan(g), 2 * f_alt[None, :] - np.nan_to_num(g), 0.0)
    with (work / "pcs" / "samples.tsv").open() as f:
        order = [r["IID"] for r in csv.DictReader(f, delimiter="\t")]
    position = {s: i for i, s in enumerate(samples)}
    summary = {}
    for pgs_id in ids:
        a = np.array([alt_weight[v][pgs_id] for v in snv_ids])
        correction = (delta @ a)[[position[s] for s in order]]
        np.save(out / f"{pgs_id}.overlap_correction.npy", correction)
        summary[pgs_id] = {"rows_blocked_per_sample_mean": float((blocked & (a != 0)[None, :]).sum(1).mean()),
                           "max_abs_correction": float(np.abs(correction).max())}  # fmt: skip
        print(pgs_id, summary[pgs_id], flush=True)
    (work_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "step", choices=("subset", "training", "pcs", "score", "overlaps", "package")
    )
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--panel", type=Path, help="pgsc_HGDP+1kGP_v1 folder (GRCh38 files)"
    )
    parser.add_argument(
        "--ancestry", type=Path, help="wgsextract-ancestry-hgdp1kg-v1.npz"
    )
    parser.add_argument(
        "--training", type=Path, help="training.tsv from training_ids.py or training"
    )
    parser.add_argument(
        "--base",
        type=Path,
        help="training, package: the published reference to rebuild --ids in",
    )
    parser.add_argument("--scores", type=Path, help="folder of {PGS}_GRCh38.txt.gz")
    parser.add_argument("--ids", nargs="*", default=list(pgs.CURATED.values()))
    parser.add_argument("--chrom", help="subset, score: one chromosome only (sizing)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory", type=int, default=8000, help="plink2 MiB")
    args = parser.parse_args()
    global _threads, _memory
    _threads, _memory = args.threads, args.memory
    if args.step == "subset":
        subset(args.work, args.panel, args.ancestry, args.scores, args.ids, args.chrom)
    elif args.step == "training":
        training(args.work, args.panel, args.ancestry, args.base)
    elif args.step == "pcs":
        pcs(args.work, args.panel, args.ancestry, args.training)
    elif args.step == "score":
        score(args.work, args.panel, args.scores, args.ids, args.chrom)
    elif args.step == "overlaps":
        overlaps(args.work, args.panel)
    else:
        from package import package

        package(args.work, args.ancestry, args.ids, args.base)


_threads = 4
_memory = 8000

if __name__ == "__main__":
    main()
