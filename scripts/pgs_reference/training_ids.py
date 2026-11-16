"""The `qc ancestry` reference's training samples, by ID (issue #617).

ancestry-ref-1 stores the training samples' PCA scores in training order but
not their IDs. ``build.py prepare`` (scripts/ancestry_reference/) chose them
deterministically from gnomAD's public sample metadata, PCA outliers and
related samples, and kept them in the LD-pruned MatrixTable's column order.
This fetches those (a few MB; not the genotypes) and repeats the selection,
writing ``training.tsv`` (training row, ID, population, region) for
``build.py pcs``.

Run with the ancestry reference's Pixi environment (Hail):

    pixi run -m scripts/ancestry_reference/pixi.toml python \
        scripts/pgs_reference/training_ids.py --work DIR
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ancestry_reference"))

import build as ancestry_build  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    data = args.work / "data"
    for name in (ancestry_build.META, ancestry_build.OUTLIERS):
        (size,) = [s for n, s in ancestry_build._list(name) if n == name]
        ancestry_build._download(name, size, data / Path(name).name)
    for prefix, folder in (
        (ancestry_build.RELATED, "related_sample_ids.ht"),
        # The MatrixTable's column table reads its globals from ../globals.
        (ancestry_build.MT + "cols/", "mt/cols"),
        (ancestry_build.MT + "globals/", "mt/globals"),
    ):
        for name, size in ancestry_build._list(prefix):
            ancestry_build._download(name, size, data / folder / name[len(prefix) :])
    import hail as hl

    hl.init(master="local[2]", quiet=True)
    related = hl.read_table(str(data / "related_sample_ids.ht"))
    (data / "related_sample_ids.txt").write_text(
        "".join(
            s + "\n" for s in sorted(related.aggregate(hl.agg.collect(related.node.s)))
        )
    )
    columns = hl.read_table(str(data / "mt" / "cols")).s.collect()
    plink = args.work / "plink"
    plink.mkdir(parents=True, exist_ok=True)
    (plink / "hgdp1kg.fam").write_text(
        "".join(f"0\t{s}\t0\t0\t0\t-9\n" for s in columns)
    )
    # prepare() without its plink2 steps: the sample selection only.
    ancestry_build._plink = lambda *a: None
    (args.work / "sets").mkdir(exist_ok=True)
    (args.work / "sets" / "train.snplist").write_text("")
    ancestry_build.prepare(args.work)
    meta = ancestry_build.read_metadata(args.work)
    train = [
        line.split()[1]
        for line in (args.work / "sets" / "train.txt").read_text().splitlines()
    ]
    with (args.work / "training.tsv").open("w") as f:
        f.write("training_row\tIID\tpopulation\tregion\n")
        for i, s in enumerate(train):
            f.write(f"{i}\t{s}\t{meta[s]['hgdp_tgp_meta.Population']}\t"
                    f"{meta[s]['hgdp_tgp_meta.Genetic.region']}\n")  # fmt: skip
    print(f"{len(train)} training samples -> {args.work / 'training.tsv'}")


if __name__ == "__main__":
    main()
