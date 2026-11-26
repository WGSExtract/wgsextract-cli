## New

- `panel audit` can compare a research gene group's qualifying allele copies
  with coding-length-matched groups in the same genome. Its GRCh38 MANE
  exon model is only fetched with `--download`, and gVCF callability is
  reported separately from the model-length fallback. For XY samples,
  matching separates diploid and haploid X regions and counts a homozygous
  ALT call in a haploid region as one copy.
