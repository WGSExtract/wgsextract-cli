## Fixes

- `qc roh` now shows why tabix could not index the allele-frequency table and
  suggests sorting only when tabix finds an ordering problem. VCF indexing
  errors also no longer suggest sorting when tabix reports a different failure
  ([#1519, misleading tabix errors](https://github.com/WGSExtract/wgsextract-cli/issues/1519)).
