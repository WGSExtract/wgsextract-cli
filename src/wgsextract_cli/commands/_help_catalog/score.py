"""Help entries for ``score``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "score pgs": _h(
        "<input stem>_<PGS ID>_pgs.json and .tsv: raw score, variant match rate, "
        "licence and citation; for curated GRCh38 scores, an ancestry-adjusted "
        "percentile.",
        "wgsextract score pgs --input sample.vcf.gz --build GRCh38 --trait height",
        "wgsextract score pgs --input sample.vcf.gz --build GRCh38 --pgs-id PGS000018",
    ),
}
