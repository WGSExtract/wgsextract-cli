## New

- `typing str --loci HTT,FMR1,AR` sizes only the named catalog loci.
  `--format parquet` writes a typed repeat table, and the result JSON
  includes numeric allele sizes, confidence intervals and read support.
  The personal report now summarizes flagged, unconfirmed and normal
  repeat calls ([#1286, structured repeat sizing](https://github.com/WGSExtract/wgsextract-cli/issues/1286)).
- Report findings can name the new `repeat_expansions` section, so the
  experimental section field of every report section's findings accepts
  that value:
  `wgsextract.report/1:sections.acmg_research.findings[].section`,
  `wgsextract.report/1:sections.ancestry.findings[].section`,
  `wgsextract.report/1:sections.carrier_screening.findings[].section`,
  `wgsextract.report/1:sections.clinvar.findings[].section`,
  `wgsextract.report/1:sections.lineage.findings[].section`,
  `wgsextract.report/1:sections.microarray.findings[].section`,
  `wgsextract.report/1:sections.overview.findings[].section`,
  `wgsextract.report/1:sections.pharmacogenomics.findings[].section`,
  `wgsextract.report/1:sections.structural_variants.findings[].section` ([#1286, structured repeat sizing](https://github.com/WGSExtract/wgsextract-cli/issues/1286)).
