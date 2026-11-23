## New

- `vcf table` adds `ad_ref` and `ad_alt`, the sample's FORMAT/AD read counts
  behind `alt_fraction`, for allele-balance and mosaicism queries. They stay
  empty when AD is missing or incomplete. [Variant table](../../variant-table.md)
  has new example queries for ancestry-common variants, cis/trans phasing and
  skewed allele balance
  ([#1289, vcf table phase, allele fraction and transcript columns](https://github.com/WGSExtract/wgsextract-cli/issues/1289)).

## Fixes

- A call whose FORMAT/AD counts no informative read (`0,0`), or misses a
  count with no FORMAT/DP, no longer reads as allele fraction 0 or 1 in
  confidence labels. It uses the caller's VAF or AF, or else says
  `allele_balance_unknown` rather than `allele_imbalance`
  ([#1289, vcf table phase, allele fraction and transcript columns](https://github.com/WGSExtract/wgsextract-cli/issues/1289)).
