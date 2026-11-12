"""The `package` step of build.py: one .npz file with everything `qc ancestry`
(and later polygenic scores, #617) needs. Loaded with allow_pickle=False."""

from __future__ import annotations

import gzip
import json
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
from build import (
    CHAINS,
    REFERENCE_VERSION,
    _sha256,
    read_bed,
    read_metadata,
)

# Component names (ADMIXTURE components are unordered, so they are
# recognised by the reference population with the highest mean share). Named
# after the reference groups where they are most common, not an ethnicity.
# K=7: by that population's gnomAD genetic region.
REGION_NAMES = {
    "AFR": "Sub-Saharan African",
    "AMR": "Indigenous American",
    "CSA": "Central and South Asian",
    "EAS": "East Asian",
    "EUR": "European",
    "MID": "Middle Eastern",
    "OCE": "Oceanian",
}
# K=12 splits regions; its components are recognised by the populations that
# carry them most.
NAMES_12 = {
    ("Dai", "CDX", "KHV", "Cambodian", "Lahu"): "Southeast Asian",
    ("Oroqen", "JPT", "Japanese", "Yakut", "Daur", "Hezhen"): "Northeast Asian",
    ("Mandenka", "GWD", "MSL"): "West African (Senegambia)",
    ("ESN", "Yoruba", "YRI", "LWK", "BantuKenya"): "West-Central and East African",
    ("San", "MbutiPygmy", "BiakaPygmy"): "Central and Southern African forager",
    ("Pima", "Maya"): "Indigenous American (North and Central)",
    ("Surui", "Karitiana"): "Indigenous American (Amazonian)",
    ("ITU", "STU", "GIH", "BEB"): "South Asian",
    ("Kalash", "Brahui", "Balochi", "Makrani"): "Central Asian and Iranian plateau",
    ("Bedouin", "Mozabite", "Druze", "Palestinian"): "Middle Eastern and Mediterranean",
    ("FIN", "Orcadian", "Russian", "GBR", "CEU"): "Northern European",
    ("Papuan", "Melanesian"): "Oceanian",
}
PCS = 10
FASTA = {
    "GRCh38": "/mnt/nas/genetics/reference/genomes/hg38.fa.gz",
    "GRCh37": "/mnt/nas/genetics/reference/genomes/hs37d5.fa.gz",
    "CHM13": "/mnt/nas/genetics/reference/genomes/chm13v2.0.fa.gz",
}
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _chain(work: Path, build: str) -> Path:
    path = work / "chains" / Path(CHAINS[build]).name
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(CHAINS[build], path)
    return path


def _reference_bases(fasta: str, loci: list[tuple[str, int]]) -> list[str]:
    """The base at each locus (contigs named as the FASTA names them)."""
    regions = "".join(f"{c}:{p}-{p}\n" for c, p in loci)
    out = subprocess.run(
        ["samtools", "faidx", fasta, "-r", "/dev/stdin", "-n", "1000"],
        input=regions, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [line.upper() for line in out.splitlines() if not line.startswith(">")]


def _fasta_contig(fasta: str, chrom: int) -> str:
    names = {
        line.split("\t")[0] for line in Path(fasta + ".fai").read_text().splitlines()
    }
    return f"chr{chrom}" if f"chr{chrom}" in names else str(chrom)


def _lift(work: Path, build: str, chrom: np.ndarray, pos: np.ndarray, ref: list[str]):
    """(chrom, pos) on ``build``, 0 where a site doesn't lift one-to-one onto
    the same strand with the same reference base."""
    from pyliftover import LiftOver

    lift = LiftOver(str(_chain(work, build)))
    out_chrom = np.zeros(len(pos), dtype=np.uint8)
    out_pos = np.zeros(len(pos), dtype=np.int32)
    for i, (c, p) in enumerate(zip(chrom, pos, strict=True)):
        hits = lift.convert_coordinate(f"chr{c}", int(p) - 1)
        if not hits or len(hits) != 1 or hits[0][2] != "+":
            continue
        target = hits[0][0].removeprefix("chr")
        if target.isdigit() and 1 <= int(target) <= 22:
            out_chrom[i], out_pos[i] = int(target), hits[0][1] + 1
    lifted = np.flatnonzero(out_pos)
    fasta = FASTA[build]
    loci = [(_fasta_contig(fasta, int(out_chrom[i])), int(out_pos[i])) for i in lifted]
    bases = _reference_bases(fasta, loci)
    for i, base in zip(lifted, bases, strict=True):
        if base != ref[i]:
            out_chrom[i] = out_pos[i] = 0
    return out_chrom, out_pos


def _quantise(p: np.ndarray) -> np.ndarray:
    return np.round(np.clip(p, 0, 1) * 65535).astype(np.uint16)


def _pca(genotypes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Allele frequencies, SNP loadings and sample scores of the standardised
    genotypes (missing calls at the mean), as plink --pca does; in chunks of
    sites to bound memory."""
    called = (genotypes >= 0).sum(axis=0)
    freq = np.where(genotypes > 0, genotypes, 0).sum(axis=0, dtype=np.int64) / (
        2 * called
    )
    scale = np.sqrt(2 * freq * (1 - freq))

    def chunk(start: int, stop: int) -> np.ndarray:
        g = genotypes[:, start:stop].astype(np.float32)
        x = (g - 2 * freq[start:stop]) / scale[start:stop]
        x[genotypes[:, start:stop] < 0] = 0
        return x.astype(np.float32)

    step = 10_000
    n, m = genotypes.shape
    gram = np.zeros((n, n), dtype=np.float64)
    for start in range(0, m, step):
        x = chunk(start, start + step)
        gram += x @ x.T
    values, vectors = np.linalg.eigh(gram)
    order = np.argsort(values)[::-1][:PCS]
    values, vectors = values[order], vectors[:, order]
    loadings = np.concatenate(
        [(chunk(s, s + step).T @ vectors) / np.sqrt(values) for s in range(0, m, step)]
    )
    scores = vectors * np.sqrt(values)
    return (
        freq.astype(np.float32),
        loadings.astype(np.float32),
        scores.astype(np.float32),
    )


def _components(k: int, q: np.ndarray, samples: list[str], meta) -> list[dict]:
    by_population: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(samples):
        by_population[meta[s]["hgdp_tgp_meta.Population"]].append(i)
    populations = sorted(by_population)
    means = np.array([q[by_population[p]].mean(axis=0) for p in populations])
    components = []
    for j in range(k):
        top = [populations[i] for i in np.argsort(-means[:, j])[:5]]
        region = meta[samples[by_population[top[0]][0]]]["hgdp_tgp_meta.Genetic.region"]
        if k == 7:
            name = REGION_NAMES[region]
        else:
            name = next((v for pops, v in NAMES_12.items() if top[0] in pops), None)
        if name is None:
            raise SystemExit(
                f"K={k} component {j}: no name for {top}; add one to NAMES"
            )
        components.append({
            "name": name,
            "region": region,
            "top_populations": {p: round(float(means[populations.index(p), j]), 3) for p in top},
        })  # fmt: skip
    if len({c["name"] for c in components}) != k:
        raise SystemExit(f"K={k}: two components got one name: {components}")
    return components


def package(work: Path, ks: list[int]) -> None:
    meta = read_metadata(work)
    genotypes, samples, bim = read_bed(work / "sets" / "train")
    chrom = np.array([int(b[0].removeprefix("chr")) for b in bim], dtype=np.uint8)
    pos = np.array([int(b[3]) for b in bim], dtype=np.int32)
    alt = [b[4] for b in bim]
    ref = [b[5] for b in bim]
    order = np.lexsort((pos, chrom))
    assert (order == np.arange(len(order))).all(), "sites must be in genome order"
    bases = _reference_bases(
        FASTA["GRCh38"], [(f"chr{c}", int(p)) for c, p in zip(chrom, pos, strict=True)]
    )
    assert bases == ref, "GRCh38 reference alleles don't match hg38"
    arrays: dict[str, np.ndarray] = {
        "chrom_GRCh38": chrom, "pos_GRCh38": pos,
        "ref": np.array(ref, dtype="S1"), "alt": np.array(alt, dtype="S1"),
    }  # fmt: skip
    lifted = {}
    for build in ("GRCh37", "CHM13"):
        arrays[f"chrom_{build}"], arrays[f"pos_{build}"] = _lift(
            work, build, chrom, pos, ref
        )
        lifted[build] = int((arrays[f"pos_{build}"] > 0).sum())
    components = {}
    for k in ks:
        p = np.loadtxt(work / "adamixture" / "train" / f"train.{k}.P")
        q = np.loadtxt(work / "adamixture" / "train" / f"train.{k}.Q")
        arrays[f"P{k}"] = _quantise(p)
        components[str(k)] = _components(k, q, samples, meta)
    freq, loadings, scores = _pca(genotypes)
    arrays["pca_frequency"], arrays["pca_loadings"], arrays["pca_scores"] = (
        freq,
        loadings,
        scores,
    )
    arrays["pca_population"] = np.array(
        [meta[s]["hgdp_tgp_meta.Population"] for s in samples]
    )
    arrays["pca_region"] = np.array(
        [meta[s]["hgdp_tgp_meta.Genetic.region"] for s in samples]
    )
    info = {
        "version": REFERENCE_VERSION,
        "source": "gnomAD v3.1 HGDP+1KG LD-pruned genotypes (Koenig et al. 2024, Genome Research), "
        "gs://gcp-public-data--gnomad/release/3.1/secondary_analyses/hgdp_1kg/; CC0 1.0",
        "training_samples": len(samples),
        "sites": len(pos),
        "lifted": lifted,
        "trainer": "ADAMIXTURE 1.9.3 (BSD-3), seed 620, 3 initialisations",
        "p_encoding": "uint16, alt-allele frequency * 65535",
        "components": components,
    }
    arrays["info"] = np.array(json.dumps(info))
    out = work / f"wgsextract-ancestry-hgdp1kg-v{REFERENCE_VERSION}.npz"
    np.savez_compressed(out, **arrays)
    print(json.dumps({k: v for k, v in info.items() if k != "components"}, indent=1))
    print(f"{out}: {out.stat().st_size:,} bytes, sha256 {_sha256(out)}")
    with gzip.open(out.with_suffix(".components.json.gz"), "wt") as f:
        json.dump(info["components"], f, indent=1)
