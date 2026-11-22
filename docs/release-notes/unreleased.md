<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `extract ydna-bam`, `extract y-mt-extract` and `extract region --target
  y|ymt` take `--y-scope associated`, which also keeps the reads on the
  reference's Y-associated contigs (GRCh38 `chrY_KI270740v1_random`, under its
  UCSC, GenBank or RefSeq name), as WGS Extract v4's Y and Y+MT BAMs did. A
  contig is kept only when its length (and checksum, if recorded) match GRCh38's; outputs are
  `_Y_associated.*` and the result lists each considered contig and why. The
  default `--y-scope primary` is unchanged
  ([#1184, Y-associated contig preset](https://github.com/WGSExtract/wgsextract-cli/issues/1184)).

## Fixes

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
- `score pgs` now finds bcftools the same way its dependency check does, so
  it scores when bcftools is installed only in the WGS Extract Pixi
  environment rather than on `PATH`. It previously failed with "No such file
  or directory: 'bcftools'"
  ([#1233, PGS bcftools probes](https://github.com/WGSExtract/wgsextract-cli/issues/1233)).
