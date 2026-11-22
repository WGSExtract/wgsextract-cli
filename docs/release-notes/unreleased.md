<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `wgsextract deps ready -- COMMAND ...` reports whether a command line can
  run on this computer without the network: its tools (version, Conda package
  and recorded licence), Docker images in the local engine, NVIDIA GPUs and
  driver, Clair3 models and wgse-deepvariant weights against their pinned
  SHA-256, and its input, reference genome, indexes and annotation data. Each
  item is runnable, installed, unverified, unsupported, missing, stale or
  incompatible, with the explicit setup or download step that provides a
  missing one. It never installs, pulls or downloads, and isn't offered by
  the server
  ([#1183, offline readiness report](https://github.com/WGSExtract/wgsextract-cli/issues/1183)).

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
