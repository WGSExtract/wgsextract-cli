## New

- `panel check` validates a research panel file (`wgsextract.panel/1`, TOML
  or JSON): gene groups with variant filters, watched sites and haplotypes.
  It lists every problem with its location, resolves the genes offline in the
  bundled NCBI RefSeq annotations with their coding lengths, and warns about
  unknown or ambiguous gene names. Research use only; it reads no sample data.
  The format's JSON Schema is `assets/schemas/wgsextract.panel.1.schema.json`
  ([#1287, declarative research gene-panel audit](https://github.com/WGSExtract/wgsextract-cli/issues/1287)).
- The bundled gene annotations now carry each gene's HGNC ID and the coding
  length of its MANE Select (else RefSeq Select, else longest) transcript, and
  `panel bed` accepts HGNC IDs (`HGNC:613`) as well as symbols
  ([#1287](https://github.com/WGSExtract/wgsextract-cli/issues/1287)).
