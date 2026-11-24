## Fixes

- `vcf deepvariant` can call decoy-rich references such as hs38DH when the
  ploidy policy excludes a contig. It passes the selected intervals in a BED
  file instead of exceeding GNU parallel's command-line limit
  ([#1491, oversized DeepVariant region selection](https://github.com/WGSExtract/wgsextract-cli/issues/1491)).
