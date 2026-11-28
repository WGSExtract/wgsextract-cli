## New

- Opt in to offline GRCh38 GTEx v10 SuSiE eQTL matches with `vcf gtex` or
  `vcf chain-annotate --annotations gtex`. Install the pinned public track
  explicitly with `ref gtex --download`. The TSV preserves each exact ALT,
  gene, tissue, credible set, PIP and reported aFC
  ([#1662, GTEx eQTL annotation](https://github.com/WGSExtract/wgsextract-cli/issues/1662)).
