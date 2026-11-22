## Fixes

- `score pgs` now finds bcftools the same way its dependency check does, so
  it scores when bcftools is installed only in the WGS Extract Pixi
  environment rather than on `PATH`. It previously failed with "No such file
  or directory: 'bcftools'"
  ([#1233, PGS bcftools probes](https://github.com/WGSExtract/wgsextract-cli/issues/1233)).
