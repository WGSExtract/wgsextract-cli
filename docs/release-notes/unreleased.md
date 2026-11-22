<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `wgsextract lookup` (also the typed API's `api.lookup` and the server's
  read-only `lookup` operation over HTTP and MCP) answers one sample's
  genotype at an rsID, a build-qualified locus or a gene: the normalised
  alleles, depth, quality, FILTER and phase, whether it is a confident call,
  confident reference, weak evidence, a no-call or not assessed (a site a
  variants-only VCF doesn't list is never taken as reference), and what the
  sample's personal report says there, attributed, including analyses not
  run yet. Identifiers resolve offline (dbSNP build 157 merge history, the
  chip rsID placements of `ref microarray-targets`, NCBI RefSeq genes); an
  identifier with several matches lists them instead of picking one. See
  [docs/lookup.md](../lookup.md)
  ([#1178, genotype lookup](https://github.com/WGSExtract/wgsextract-cli/issues/1178)).

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
