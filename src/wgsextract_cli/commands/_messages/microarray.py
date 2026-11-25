"""Messages for the microarray commands."""

CLI_HELP = {
    "cmd_microarray": "Simulate a consumer microarray (like 23andMe or AncestryDNA) from your WGS data.",
    "micro_formats_help": """Comma-separated list of formats to generate (default: all).
Variants are named by letter (h600.org's Microarray File Formats); similar
minor variants are merged into one file, e.g. ancestry_v2ab.
Available formats:
  Combined kits:
    all, combined_v5 (CombinedKit v5: every chip's markers in one 23andMe-style
    file, e.g. for GEDmatch; always written, writes no vendor files alone)
    combined_v3 (CombinedKit v3: classic 14-template subset of CombinedKit v5,
    written as <sample>_CombinedKit_v3.txt)
  23andMe:
    23andme_v2 (also v1: same markers), 23andme_v3, 23andme_v4 (v4a-c),
    23andme_v5, 23andme_v3+v5, 23andme_api
  AncestryDNA:
    ancestry_v1, ancestry_v2ab (v2a and v2b merged; alias: ancestry_v2),
    ancestry_v2cd (v2c and v2d merged), ancestry_v2e
  Family Tree DNA:
    ftdna_v1 (Affymetrix, NCBI36 positions), ftdna_v2 (v2a-d), ftdna_v3, ftdna_v4
    ftdna_v2y, ftdna_v3y, ftdna_v4y (the separate Y files, GRCh38 positions)
  Living DNA:
    ldna_v1, ldna_v2
  MyHeritage:
    myheritage_v1, myheritage_v2, myheritage_v3
  Other vendors:
    tellmegen, genera, mthfr (MTHFR Genetics), meudna_v1, meudna_v2,
    selfdecode, nucleus
  AADR v66.p1 (GRCh37 outputs, PLINK .bed/.bim/.fam AND EIGENSTRAT .geno/.snp/.ind):
    aadr_1240k, aadr_ho, aadr_1240k+ho (HO is a subset of 1240K in this release)
    aadr_2m (opt-in; first run 'ref aadr --install'; v66.p1 genetic distances
      differ from legacy 1240K/HO at 1,200,251 sites; maps are never mixed)
""",
}

LOG_MESSAGES = {
    "micro_liftover_warn": "Input build is hg38. Liftover to hg19 may be required for some formats.",
    "micro_generating_fmt": "Generating {format} output...",
}
