"""Help entries for ``lineage``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "lineage y-haplogroup": _h(
        "yleaf/: hg_prediction.hg, the per-sample .out/.fmf/.info reports and "
        "run.json; prints the haplogroup and the tree's name and version.",
        "wgsextract lineage y-haplogroup --input sample.cram",
    ),
    "lineage y-private": _h(
        "y_private/: <sample>.y_private.tsv (candidates, with the marker sets, "
        "callable mask and filters in its header) and <sample>.y_private.vcf.gz "
        "(+ .tbi).",
        "wgsextract lineage y-private --input sample.cram",
        "wgsextract lineage y-private --input sample.vcf.gz --build t2t --download",
    ),
    "lineage y-str": _h(
        "ystr/: <sample>.ystr.tsv, <sample>.ftdna.csv (FTDNA marker order) and "
        "run.json.",
        "wgsextract lineage y-str --input sample.cram --panel Y-37",
    ),
    "lineage mt-haplogroup": _h(
        "haplogrep_results.txt; prints the mtDNA haplogroup and Haplogrep 3 tree. "
        "With --contamination, also haplocheck_contamination.txt (+ .raw.txt, "
        ".html).",
        "wgsextract lineage mt-haplogroup --input sample.cram",
        "wgsextract lineage mt-haplogroup --input sample.cram --contamination",
        "wgsextract lineage mt-haplogroup --input sample.vcf.gz --tree mitotree-rcrs",
    ),
}
