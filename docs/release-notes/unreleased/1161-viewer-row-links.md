## Desktop app

- A Personal Report run lists its findings, and an mtDNA haplogroup run the
  variants that place it, under the result, each with **Show in viewer**. A
  row opens the sample's own BAM, CRAM or VCF at that variant, offline, with
  the contig spelled as the matched reference spells it (`chr1` or `1`,
  `chrM` or `MT`); a ClinVar or ACMG finding opens only on a reference of
  its evidence's build. A row without a single position, or whose sample file is
  missing, unindexed or outside My Genomes, shows a disabled link with the
  reason. Haplogroup positions, numbered on rCRS, open only on an rCRS chrM, never
  on hg19's Yoruba or T2T's CHM13 chrM ([#1161, viewer links from result tables](https://github.com/WGSExtract/wgsextract-cli/issues/1161)).
