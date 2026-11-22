<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `qc roh` finds runs of homozygosity in a VCF with `bcftools roh` and the
  population allele frequencies you give it (`--af-file`: a gnomAD or 1000
  Genomes sites VCF, or a bcftools AF table, on the sample's build). It
  reports runs, marker evidence, F_ROH over the declared scope only, and the
  frequency file's release and digest, and flags a long run over an
  imprinted region (15q11-q13, 11p15.5, 14q32.2, GNAS and others) for review
  as possible isodisomy, which is not a diagnosis. It never downloads
  anything; see [Runs of homozygosity](../roh.md)
  ([#1174, ROH and isodisomy flags](https://github.com/WGSExtract/wgsextract-cli/issues/1174)).

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
