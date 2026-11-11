"""Build the `qc ancestry` reference (issue #620) from gnomAD's HGDP+1KG release.

Run each step in order with this folder's Pixi environment (Hail, Java,
ADAMIXTURE, plink2), on a machine with ~10 GB of RAM:

    pixi run -m scripts/ancestry_reference/pixi.toml python \
        scripts/ancestry_reference/build.py STEP --work DIR

Steps:

- ``fetch``: the LD-pruned genotypes of the 4,151 HGDP+1KG samples (a Hail
  MatrixTable, ~7 GB in ~200k objects) and the sample metadata from gnomAD's
  public bucket (``gs://gcp-public-data--gnomad``, served over HTTPS).
- ``export``: the MatrixTable's autosomal biallelic SNVs as PLINK files (Hail).
- ``prepare``: sample QC as Koenig et al.'s PCA (no gnomAD hard filter,
  unrelated, not a PCA outlier; gnomAD's own outlier QC drops whole HGDP
  populations, such as the Papuans, so it isn't used), a held-out tenth of each 1000 Genomes population for validation,
  and variant QC on the training samples (call rate >= 98%, MAF >= 1%).
- ``train``: ADAMIXTURE at each K (default 7 and 12) on the training samples,
  and on training + held-out samples (the "full" fit validation compares
  against).
- ``package``: the reference file: sites on GRCh38, GRCh37 and T2T-CHM13
  (lifted over with Hail, alleles checked against each FASTA), P quantised to
  uint16 per K, component labels and the reference populations' mean
  proportions, and a PCA of the training samples for later scoring (#617).
  Prints its SHA-256 for src/wgsextract_cli/core/ancestry_reference.py.

The data are gnomAD v3.1 HGDP+1KG (Koenig et al. 2024, Genome Research),
released by gnomAD without access restrictions under CC0 1.0.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

BUCKET = "gcp-public-data--gnomad"
ROOT = "release/3.1/secondary_analyses/hgdp_1kg/"
MT = ROOT + "pca_preprocessing/ld_pruned.mt/"
META = ROOT + "metadata_and_qc/gnomad_meta_v1.tsv"
OUTLIERS = ROOT + "pca/pca_outliers.txt"
RELATED = ROOT + "pca_preprocessing/related_sample_ids.ht/"
HERE = Path(__file__).resolve().parent
HOLDOUT_FRACTION = 0.1
SEED = 620
# Held out too: the 1000 Genomes sample whose public 30x CRAM validates the
# whole pipeline, from reads to proportions.
FORCED_HOLDOUT = ("HG00096",)
REFERENCE_VERSION = 1
CHAINS = {
    "GRCh37": "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHg19.over.chain.gz",
    "CHM13": "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/liftOver/hg38ToHs1.over.chain.gz",
}


def _http(name: str) -> str:
    return f"https://storage.googleapis.com/{BUCKET}/{urllib.parse.quote(name)}"


def _list(prefix: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    token = None
    while True:
        query = {"prefix": prefix, "fields": "items(name,size),nextPageToken"}
        if token:
            query["pageToken"] = token
        url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o?"
        with urllib.request.urlopen(url + urllib.parse.urlencode(query)) as r:
            page = json.load(r)
        items += [(i["name"], int(i["size"])) for i in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            return items


def _download(name: str, size: int, dest: Path) -> None:
    if dest.is_file() and dest.stat().st_size == size:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(_http(name), timeout=120) as r:
                partial.write_bytes(r.read())
            break
        except OSError:
            if attempt == 4:
                raise
    if partial.stat().st_size != size:
        raise RuntimeError(f"{name}: got {partial.stat().st_size} bytes, not {size}")
    partial.replace(dest)


def fetch(work: Path) -> None:
    data = work / "data"
    objects = _list(MT)
    with ThreadPoolExecutor(32) as pool:
        list(
            pool.map(
                lambda o: _download(
                    o[0], o[1], data / "ld_pruned.mt" / o[0][len(MT) :]
                ),
                objects,
            )
        )
    for name in (META, OUTLIERS):
        (size,) = [s for n, s in _list(name) if n == name]
        _download(name, size, data / Path(name).name)
    for name, size in _list(RELATED):
        _download(name, size, data / "related_sample_ids.ht" / name[len(RELATED) :])
    print(f"{len(objects)} MatrixTable objects in {data / 'ld_pruned.mt'}")


def export(work: Path, threads: int) -> None:
    """The genotypes as PLINK files, and the related samples gnomAD's PCA
    left out (Koenig et al. 2024) as a list."""
    (work / "plink").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-c",
         "import hail as hl, sys; hl.init(master='local[2]', quiet=True); "
         "t = hl.read_table(sys.argv[1]); "
         "open(sys.argv[2], 'w').write(''.join(s + '\\n' for s in sorted(t.aggregate(hl.agg.collect(t.node.s)))))",
         str(work / "data" / "related_sample_ids.ht"),
         str(work / "data" / "related_sample_ids.txt")],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [sys.executable, str(HERE / "export_plink.py"),
         str(work / "data" / "ld_pruned.mt"), str(work / "plink" / "hgdp1kg"),
         str(threads)],
        check=True,
    )  # fmt: skip


def _plink(*args: str) -> None:
    subprocess.run(["plink2", "--silent", *args], check=True)


def read_metadata(work: Path) -> dict[str, dict[str, str]]:
    with open(work / "data" / "gnomad_meta_v1.tsv", newline="") as f:
        return {row["s"]: row for row in csv.DictReader(f, delimiter="\t")}


def prepare(work: Path) -> None:
    plink = work / "plink"
    meta = read_metadata(work)
    outliers = set((work / "data" / "pca_outliers.txt").read_text().split())
    related = set((work / "data" / "related_sample_ids.txt").read_text().split())
    fam = [line.split()[1] for line in (plink / "hgdp1kg.fam").read_text().splitlines()]
    kept = [
        s
        for s in fam
        if s in meta
        and meta[s]["sample_filters.hard_filtered"] == "false"
        and s not in related
        and s not in outliers
        and meta[s]["hgdp_tgp_meta.Genetic.region"] not in ("", "NA")
    ]
    by_population: dict[str, list[str]] = defaultdict(list)
    for s in kept:
        if meta[s]["hgdp_tgp_meta.Project"] == "1000 Genomes":
            by_population[meta[s]["hgdp_tgp_meta.Population"]].append(s)
    rng = random.Random(SEED)
    holdout: set[str] = set()
    for population in sorted(by_population):
        samples = sorted(by_population[population])
        holdout.update(rng.sample(samples, round(len(samples) * HOLDOUT_FRACTION)))
    holdout |= {s for s in FORCED_HOLDOUT if s in kept}
    train = [s for s in kept if s not in holdout]
    sets = work / "sets"
    sets.mkdir(exist_ok=True)
    for name, samples in (
        ("train", train),
        ("holdout", sorted(holdout)),
        ("all", kept),
    ):
        (sets / f"{name}.txt").write_text("".join(f"0\t{s}\n" for s in samples))
    _plink("--bfile", str(plink / "hgdp1kg"), "--keep", str(sets / "train.txt"),
           "--geno", "0.02", "--maf", "0.01", "--write-snplist", "--out",
           str(sets / "train"))  # fmt: skip
    for name in ("train", "holdout", "all"):
        _plink("--bfile", str(plink / "hgdp1kg"), "--keep", str(sets / f"{name}.txt"),
               "--extract", str(sets / "train.snplist"), "--make-bed",
               "--out", str(sets / name))  # fmt: skip
    regions = Counter(meta[s]["hgdp_tgp_meta.Genetic.region"] for s in train)
    print(f"samples: {len(fam)} -> {len(kept)} kept, {len(train)} train, "
          f"{len(holdout)} held out; train regions {dict(sorted(regions.items()))}")  # fmt: skip
    print(f"sites: {sum(1 for _ in open(sets / 'train.snplist'))}")


def train(work: Path, ks: list[int], threads: int, which: list[str]) -> None:
    for name in which:
        for k in ks:
            out = work / "adamixture" / name
            out.mkdir(parents=True, exist_ok=True)
            if (out / f"{name}.{k}.P").is_file():
                continue
            subprocess.run(
                ["adamixture", "--data_path", str(work / "sets" / f"{name}.bed"),
                 "--save_dir", str(out), "--name", name, "-k", str(k),
                 "-t", str(threads), "--seed", str(SEED), "--n_inits", "3"],
                check=True,
            )  # fmt: skip


def read_bed(prefix: Path) -> tuple[np.ndarray, list[str], list[list[str]]]:
    """(samples x sites) alt-allele dosages, -1 for missing; Hail writes A1 =
    alt, so a PLINK hom-A1 call (00) is 2 alt alleles."""
    samples = [
        line.split()[1] for line in prefix.with_suffix(".fam").read_text().splitlines()
    ]
    bim = [line.split() for line in prefix.with_suffix(".bim").read_text().splitlines()]
    n, m = len(samples), len(bim)
    raw = np.fromfile(prefix.with_suffix(".bed"), dtype=np.uint8)
    assert raw[:3].tolist() == [0x6C, 0x1B, 0x01], "not a SNP-major PLINK .bed"
    per = (n + 3) // 4
    packed = raw[3:].reshape(m, per)
    codes = np.stack([(packed >> s) & 3 for s in (0, 2, 4, 6)], axis=2).reshape(m, -1)[
        :, :n
    ]
    lookup = np.array([2, -1, 1, 0], dtype=np.int8)
    return lookup[codes].T.copy(), samples, bim


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "step", choices=("fetch", "export", "prepare", "train", "package")
    )
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--k", type=int, nargs="+", default=[7, 12])
    parser.add_argument("--sets", nargs="+", default=["train", "all"])
    args = parser.parse_args()
    if args.step == "fetch":
        fetch(args.work)
    elif args.step == "export":
        export(args.work, args.threads)
    elif args.step == "prepare":
        prepare(args.work)
    elif args.step == "train":
        train(args.work, args.k, args.threads, args.sets)
    else:
        from package import package

        package(args.work, args.k)


if __name__ == "__main__":
    main()
