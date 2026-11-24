## Fixes

- Variant callers no longer choose a build-specific ploidy table from a file
  name, folder name or contig count alone. Supply a ploidy explicitly if the
  input and reference cannot establish the build; `vep run` can also use an
  explicit `--assembly` ([#1346, name-only ploidy presets](https://github.com/WGSExtract/wgsextract-cli/issues/1346)).
- `vep run` now refuses a FASTA that disagrees with the BAM or CRAM header or
  `--assembly`, even when the alignment and `--assembly` agree
  ([#1435, mismatched VEP reference](https://github.com/WGSExtract/wgsextract-cli/issues/1435)).
