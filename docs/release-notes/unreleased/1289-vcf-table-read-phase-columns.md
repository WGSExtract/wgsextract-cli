## New

- `vcf table` has new columns: `transcript_id` (the chosen transcript that
  the HGVS columns describe), `phase_set` (FORMAT/PS of phased genotypes,
  for telling cis from trans), `alt_fraction`, `ad_ref` and `ad_alt` (the
  sample's read evidence, for allele-balance and mosaicism queries), and
  `gnomad_popmax_af` and `gnomad_nhomalt`. Values the VCF doesn't have stay
  empty, never 0. `vcf gnomad` with a gnomAD sites VCF now also adds its
  grpmax frequency and homozygote count, which fill the last two columns
  ([#1289, vcf table phase, allele fraction and transcript columns](https://github.com/WGSExtract/wgsextract-cli/issues/1289)).

## Fixes

- A call whose FORMAT/AD counts no informative read (`0,0`), or misses a
  count with no FORMAT/DP to fall back on, no longer reads as allele
  fraction 0 or 1. Its confidence label says `allele_balance_unknown`
  instead of `allele_imbalance`
  ([#1289, vcf table phase, allele fraction and transcript columns](https://github.com/WGSExtract/wgsextract-cli/issues/1289)).
