## New

- `methylation regions --partition-hp` summarizes HP1, HP2 and unphased
  CpG calls from an indexed haplotagged BAM or CRAM. Differences are only
  reported for a coherent phase set and sufficient shared coverage.
  The new table is `wgsextract.methylation-regions-hp/1`; it adds `partition`,
  `phase_status`, `phase_set`, `shared_qualified_cpgs` and `hp1_minus_hp2_pp`.
  The manifest records
  output `role:methylation_regions_haplotypes`, optional input
  `role:phase_blocks`, and method metadata under
  `wgsextract.result/1:details.haplotype_partition`
  ([#1293, haplotype-resolved methylation region summaries](https://github.com/WGSExtract/wgsextract-cli/issues/1293)).
