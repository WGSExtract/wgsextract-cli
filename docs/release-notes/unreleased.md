<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `info calculate-coverage --with-bins` reports the depth bins (0, 1-3, 4-7
  and 8+ reads per chromosome) after the coverage table. A run whose two
  reports are newer than the input shows them again instead of rescanning.
  The GUI's Detailed Coverage button uses it.

- `align` and `realign` write the BAM or CRAM index while sorting instead of
  reading the whole output again to index it, saving about 5 CPU-minutes on a
  30x genome. The records are unchanged
  ([#545](https://github.com/WGSExtract/wgsextract-cli/issues/545)).
