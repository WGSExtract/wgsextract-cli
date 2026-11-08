<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `align` and `realign` write the BAM or CRAM index while sorting instead of
  reading the whole output again to index it, saving about 5 CPU-minutes on a
  30x genome. The records are unchanged
  ([#545](https://github.com/WGSExtract/wgsextract-cli/issues/545)).

## Fixes

- `analyze comprehensive` no longer stops before variant calling when the Y
  haplogroup can't be called, for example on a low-coverage male genome. It
  records a `y_haplogroup_failed` warning and carries on; a rerun retries the
  Y haplogroup ([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)).
