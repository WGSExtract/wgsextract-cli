<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `report` has a mitochondrial DNA section: mtDNA heteroplasmy levels at
  reviewed variants (ClinVar expert-panel pathogenic SNVs, release
  2026-11-15) and CPIC's MT-RNR1 aminoglycoside-risk alleles, each
  homoplasmic, heteroplasmic, weak evidence, not detected (100x or more,
  ruling out 10% or more) or not assessed, with confidence, sources and a
  clinician-review advisory. A supported heteroplasmic MT-RNR1 allele counts
  as risk evidence. `lineage mt-haplogroup --contamination` on a BAM or CRAM
  now keeps mutserve's calls and per-position read table, with the mtDNA
  reference and mutserve version, for it
  ([#1175, mtDNA heteroplasmy in the report](https://github.com/WGSExtract/wgsextract-cli/issues/1175)).

## Fixes

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
