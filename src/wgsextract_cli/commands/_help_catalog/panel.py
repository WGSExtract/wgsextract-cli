"""Help entries for ``panel``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "panel bed": _h(
        "panel_<presets>[_<genes>]_<build>_pad<N>kb.bed, the genes named when "
        "up to three, else <N>genes-<digest> (BED4: contig, start, end, genes; "
        "contig names from --ref when given).",
        "wgsextract panel bed --preset pharmacogenes",
        "wgsextract panel bed --genes CYP2D6,HLA-A,HLA-B --pad-kb 50 --ref hg38.fa",
        "wgsextract panel bed --preset hla --preset cardiac --build t2t",
        "wgsextract panel bed --gene-file my_genes.txt -o panels",
        "wgsextract panel bed --list-presets",
    ),
    "panel check": _h(
        "Prints the check; writes no files (--json gives the problems, each "
        "gene's resolution and coding length, and each group's filters).",
        "wgsextract panel check --panel my_panel.toml",
        "wgsextract panel check --panel my_panel.json --build hg19",
    ),
    "panel audit": _h(
        "<sample>.panel_audit.json, <sample>.panel_audit.tsv (or Parquet) "
        "and a result manifest.",
        "wgsextract panel audit --panel my_panel.toml --vcf-input sample.vcf.gz --research-panel",
    ),
}
