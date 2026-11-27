## Fixes

- Pinned BGZF reference downloads, including the 1000 Genomes imputation
  panel, now retry damaged byte ranges instead of discarding the whole
  download. The file is installed only if its SHA-256 matches the pin
  ([#1619, corrupt imputation panel downloads](https://github.com/WGSExtract/wgsextract-cli/issues/1619)).
