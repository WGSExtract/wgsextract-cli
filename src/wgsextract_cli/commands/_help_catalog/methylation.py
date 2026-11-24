"""Help entries for ``methylation``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "methylation pileup": _h(
        "<input stem>.<context>.bedmethyl.gz (+ .tbi) and "
        "<input stem>.<context>.methylation.tsv; with --format parquet the "
        "summary is .methylation.parquet and the calls are also in "
        "<input stem>.<context>.bedmethyl.parquet.",
        "wgsextract methylation pileup --input ont.bam",
        "wgsextract methylation pileup --input ont.bam --format parquet",
    ),
    "methylation regions": _h(
        "<input stem>.<build>.<preset>.methylation.tsv (or .parquet) and its run manifest.",
        "wgsextract methylation regions -i ont.cpg.bedmethyl.gz --ref hg38.fa "
        "--build hg38 --preset promoters",
    ),
}
