## Fixes

- `report-issue` now reads sample names from a BAM/CRAM or VCF header when
  samtools or bcftools is installed only in the WGS Extract Pixi environment
  rather than on `PATH`, so it redacts them from the report. It previously
  could not start the tools and left those names in
  ([#1314, report sample redaction](https://github.com/WGSExtract/wgsextract-cli/issues/1314)).
