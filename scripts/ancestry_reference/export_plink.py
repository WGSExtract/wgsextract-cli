"""Export gnomAD's LD-pruned HGDP+1KG genotypes (a Hail MatrixTable) to PLINK.

Usage: export_plink.py <ld_pruned.mt> <out prefix> [threads]

Keeps the GT field of autosomal biallelic SNVs; samples stay in the
MatrixTable's order. Run through build.py, not directly.
"""

import sys

import hail as hl


def main() -> None:
    mt_path, out, threads = (
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3] if len(sys.argv) > 3 else 4),
    )
    hl.init(
        master=f"local[{threads}]",
        quiet=True,
        default_reference="GRCh38",
        spark_conf={"spark.driver.memory": "6g"},
        tmp_dir=out + ".hailtmp",
        local_tmpdir=out + ".hailtmp",
    )
    mt = hl.read_matrix_table(mt_path)
    mt = mt.select_rows().select_cols().select_entries("GT")
    mt = mt.filter_rows(
        mt.locus.in_autosome()
        & (hl.len(mt.alleles) == 2)
        & hl.is_snp(mt.alleles[0], mt.alleles[1])
    )
    mt = mt.naive_coalesce(200)
    hl.export_plink(
        mt,
        out,
        ind_id=mt.s,
        varid=hl.str(mt.locus.contig)
        + ":"
        + hl.str(mt.locus.position)
        + ":"
        + mt.alleles[0]
        + ":"
        + mt.alleles[1],
    )


if __name__ == "__main__":
    main()
