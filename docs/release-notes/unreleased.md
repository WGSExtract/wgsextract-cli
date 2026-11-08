<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `align` and `realign` write the BAM or CRAM index while sorting instead of
  reading the whole output again to index it, saving about 5 CPU-minutes on a
  30x genome. The records are unchanged
  ([#545](https://github.com/WGSExtract/wgsextract-cli/issues/545)).
- [A 30x genome on a 16 GB Apple M1](../apple-silicon-16gb.md) records how
  every command runs on a base Mac mini, and a whole 35x HG002 genome from
  `realign` to `report` with default settings: 7.3 hours, with SNP recall of
  99.1% and precision of 97.5% against GIAB v4.2.1
  ([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)).
