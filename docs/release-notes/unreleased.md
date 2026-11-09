<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `info calculate-coverage --with-bins` reports the depth bins (0, 1-3, 4-7
  and 8+ reads per chromosome) after the coverage table. A run whose two
  reports are newer than the input shows them again instead of rescanning.
  The GUI's Detailed Coverage button uses it.
- **Library installs can be deleted:** `ref aadr`, `ref ybrowse`,
  `ref spliceai`, `ref alphamissense` and `ref pharmgkb` take `--delete`,
  `vep delete` removes one release of the VEP cache, and the new
  `ref microarray-targets` builds (or with `--delete` removes) the genome-wide
  hg19 and hg38 microarray targets. On the app's Library page each Install
  button becomes a confirmed Delete once its item is installed.
- `ref bootstrap` also installs the AADR 2M SNP metadata and the hg19ToHg38
  chain, so microarray's AADR 2M export works on every library. A library
  bootstrapped before this shows as not bootstrapped until `ref bootstrap`
  runs again. The app no longer asks for a separate VEP cache folder: the
  cache is the reference library's `vep` folder (or config
  `vep_cache_directory`).

- `align` and `realign` write the BAM or CRAM index while sorting instead of
  reading the whole output again to index it, saving about 5 CPU-minutes on a
  30x genome. The records are unchanged
  ([#545](https://github.com/WGSExtract/wgsextract-cli/issues/545)).
