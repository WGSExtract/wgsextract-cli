<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `align` and `realign` write the BAM or CRAM index while sorting instead of
  reading the whole output again to index it, saving about 5 CPU-minutes on a
  30x genome. The records are unchanged
  ([#545](https://github.com/WGSExtract/wgsextract-cli/issues/545)).
- **Faster SNP and indel calling by default:** `vcf snp` and `vcf indel` call
  contigs in parallel when `--threads` is above 1, as `microarray` already
  did; the VCF records match a single-stream run's. On a 16 GB M1 a
  whole-genome SNP call took 211 s instead of 849 s. `--no-parallel` keeps
  one stream ([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)).
