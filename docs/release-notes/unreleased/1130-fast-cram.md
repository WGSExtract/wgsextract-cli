## New

- `align` and `realign` accept `--output-profile fast` to write lossless CRAM
  3.1 faster at the cost of larger files. In staged alignment it also uses
  level-1 compression for the internal raw BAM and budgets more disk space
  for it. Defaults and duplicate marking are unchanged
  ([#1130, alignment compression timings](https://github.com/WGSExtract/wgsextract-cli/issues/1130)).
