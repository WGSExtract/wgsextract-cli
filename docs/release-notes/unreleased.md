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
- [A 30x genome on a 16 GB Apple M1](../apple-silicon-16gb.md) records how
  every command runs on a base Mac mini, and a whole 35x HG002 genome from
  `realign` to `report` with default settings: 7.3 hours, with SNP recall of
  99.1% and precision of 97.5% against GIAB v4.2.1
  ([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)).
- **Faster SNP and indel calling by default:** `vcf snp` and `vcf indel` call
  contigs in parallel when `--threads` is above 1, as `microarray` already
  did; the VCF records match a single-stream run's. On a 16 GB M1 a
  whole-genome SNP call took 211 s instead of 849 s. Contigs without reads
  are skipped and small ones (decoys, ALTs, HLA) are called together, so
  references with thousands of contigs don't slow it down. `--no-parallel`
  keeps one stream ([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)).

## Fixes

- `realign`'s FASTQ extraction now shows progress while it runs: the share of the input read, then the reads written. Before, a whole-genome extraction showed 0% until it finished.
- `analyze comprehensive` no longer stops before variant calling when the Y
  haplogroup can't be called, for example on a low-coverage male genome. It
  records a `y_haplogroup_failed` warning and carries on; a rerun retries the
  Y haplogroup ([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)).
