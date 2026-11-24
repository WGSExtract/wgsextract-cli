## New

- `vcf table` adds `ad_ref` and `ad_alt`, the sample's FORMAT/AD read counts
  for allele-balance queries. They stay empty when AD is missing or incomplete.
  `/v1/variants` and MCP `query_variants` can return both columns.
  [Variant table](../../variant-table.md)
  has new example queries for ancestry-common variants, cis/trans phasing and
  skewed allele balance
  ([#1289, vcf table phase, allele fraction and transcript columns](https://github.com/WGSExtract/wgsextract-cli/issues/1289)).

## Fixes

- Confidence labels no longer treat zero-sum FORMAT/AD (`0,0`) or a missing
  AD count without FORMAT/DP as evidence of `allele_imbalance`. They use
  another available read-fraction measure or report `allele_balance_unknown`
  ([#1289, vcf table phase, allele fraction and transcript columns](https://github.com/WGSExtract/wgsextract-cli/issues/1289)).
