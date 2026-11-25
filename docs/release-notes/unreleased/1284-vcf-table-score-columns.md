## New

- `vcf table` exports in-silico scores as typed columns: `revel`,
  `alphamissense_score` and `alphamissense_class`, `cadd_raw` and
  `cadd_phred`, `spliceai_max` (the highest SpliceAI delta score of the ALT,
  for the row's gene), `phylop`, `gerp`, and SIFT's and PolyPhen's scores and
  predictions. They come from `vcf revel`, `vcf alphamissense`, `vcf cadd`,
  `vcf spliceai`, `vcf phylop` and `vcf gerp`, else from fastVEP's and VEP's
  own fields (plugins, dbNSFP, SIFT and PolyPhen). An unscored variant leaves
  them empty, never 0. The server's `/v1/variants` and MCP `query_variants`
  return them too
  ([#1284, vcf table in-silico score columns](https://github.com/WGSExtract/wgsextract-cli/issues/1284)).
