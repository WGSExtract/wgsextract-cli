<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

## Fixes

- `score pgs --trait bmi` now uses PGS005198, the GIANT consortium's
  multi-ancestry BMI score (Smit et al. 2025, CC BY 4.0), instead of
  PGS000027, which only academic research may use. Its ancestry-adjusted
  percentiles need the new reference release `pgs-ref-2`, so the first run
  after upgrading needs `--download` again. CC BY 4.0 scores no longer warn
  `pgs_license_restricted`
  ([#1165, BMI score](https://github.com/WGSExtract/wgsextract-cli/issues/1165)).
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
