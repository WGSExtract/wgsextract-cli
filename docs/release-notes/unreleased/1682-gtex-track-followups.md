## Fixes

- GTEx can reopen its track on Windows UNC shares, and `ref needs` and
  `vcf gtex` use the same integrity check for the installed track.
- GTEx and SCREEN cCRE annotations recognize numeric VCF assembly declarations
  (`37` and `38`), including when they conflict with the declared reference.
