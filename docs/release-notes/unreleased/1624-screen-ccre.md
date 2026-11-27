## New

- Opt in to offline GRCh38 SCREEN cCRE overlap with `vcf regulatory` or
  `vcf chain-annotate --annotations regulatory`. Install the pinned track
  explicitly with `ref regulatory --download`. VCFs gain per-ALT class and
  accession annotations, and `vcf table` exposes `ccre_class` for research
  use ([#1624, regulatory tracks](https://github.com/WGSExtract/wgsextract-cli/issues/1624)).
