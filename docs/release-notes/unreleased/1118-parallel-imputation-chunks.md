## Changed

- `vcf impute` runs GLIMPSE2's chunks side by side, one thread each, as many
  at once as `--threads` and the available memory allow (or `--memory` per
  chunk), largest first. GLIMPSE2's own threads only split work between
  samples, so the old one-chunk-at-a-time run used one core whatever
  `--threads` said; on chr20, 8 chunks at once took about a fifth of the time.
  Outputs are identical to one chunk at a time, and now reproducible at any
  `--threads` (GLIMPSE2 2.0.1's own threads sometimes changed genotypes). A
  failed chunk stops the others and is named in the error
  ([#1118, imputation speed](https://github.com/WGSExtract/wgsextract-cli/issues/1118)).
