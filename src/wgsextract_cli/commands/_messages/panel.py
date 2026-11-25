"""Messages for the panel commands."""

CLI_HELP = {
    "cmd_panel": "Turn gene lists and presets (pharmacogenes, HLA/MHC, autoimmune, ACMG SF, cardiac) into target regions, and check research panel files.",
    "cmd_panel-bed": "Write a BED of gene regions for Nanopore adaptive sampling (MinKNOW) or targeted analysis: genes and presets are padded (default 100 kb each side), clipped and merged, from the bundled NCBI RefSeq annotations of hg38, T2T or hg19. Reports the panel's share of the genome; ONT advises under 5 percent (best under 1 percent).",
    "cmd_panel-check": "Validate a research panel file (wgsextract.panel/1, TOML or JSON: gene groups with variant filters, watched sites and haplotypes) and resolve its genes in the bundled NCBI RefSeq annotations, with their coding lengths. Lists every problem with its location, and unknown or ambiguous gene names. Reads no sample data. Research use only.",
    "cmd_panel-audit": "Audit one local annotated genome against a research panel: variant filters, coding callability, watched loci and phased allele patterns. No network access. Research use only.",
}
