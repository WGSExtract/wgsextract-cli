<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

## Fixes

- `vcf table` no longer lets an empty `gnomad_af` pass for "rare" when the VCF
  carries no gnomAD frequencies. A vep-rs cache from `vep download` gives VEP
  1000 Genomes frequencies only (Ensembl publishes no gnomAD ones to build
  from); the table now keeps them in new `kg_af` and `max_af` columns, never
  in `gnomad_af`, records `population_af_sources`, and warns
  `gnomad_af_missing` naming `vcf gnomad`. `vep run` lists the missing gnomAD
  frequencies in `vep_cache_gaps`, `vep download --variations` says it gives
  1000 Genomes frequencies, and a VEP frequency listing several co-located
  variants (`0.1&0.4`) now reads as the highest instead of empty
  ([#1354, vep-rs gnomAD frequencies](https://github.com/WGSExtract/wgsextract-cli/issues/1354)).
- `ref cadd` keeps a complete CADD score table whose index download failed or
  was cancelled: the next run checks the table against its published MD5 and
  fetches only the missing index, instead of downloading the ~85 GB SNV table
  again. A table in place that fails the check is reported, not removed.
  `ref revel` now downloads over https and checks each file against a pinned
  SHA-256
  ([#1245, CADD index retry and REVEL integrity](https://github.com/WGSExtract/wgsextract-cli/issues/1245)).
- `lineage y-str` now calls DYS448, DYS459 and DYS589 on T2T when reads
  support a call. DYS589's non-repeat boundary variation no longer appears
  as a fractional STR allele. DYS425 is left blank on all builds because
  short reads cannot reliably distinguish its assay target from paralogs
  ([#1155, missing Y-STRs](https://github.com/WGSExtract/wgsextract-cli/issues/1155)).
- `lineage y-haplogroup --tree ftdna` and `--tree isogg` correct independently
  verified ancestral/derived reversals using a pinned, separately licensed
  YBrowse polarity module. This fixes inverted backbone evidence and wrong
  haplogroup calls, including reverse-strand T2T markers. Ancient panels are
  filtered after correction. Version 2 requires compatible source branches
  and reference-lineage agreement; name-only matching in the superseded
  version 1 incorrectly flipped valid sister-branch markers. Use version 2,
  not version 1. First use requires `--download`; custom marker
  panels are unchanged ([#1168, Y marker polarity](https://github.com/WGSExtract/wgsextract-cli/issues/1168)).
