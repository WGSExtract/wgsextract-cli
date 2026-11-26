## Changed

- `vcf variantqc` is now certified Validated: real DISCVRSeq 1.3.86 on Java 17
  matched bcftools counts for HG002 GRCh38 calls on Windows ([#1621, VariantQC and Tools labels](https://github.com/WGSExtract/wgsextract-cli/pull/1621)).

## New

- `deps list` marks each optional tool that ships in the CLI's own
  environment as `preinstalled`: it comes with WGS Extract and can't be
  uninstalled on its own ([#1621, VariantQC and Tools labels](https://github.com/WGSExtract/wgsextract-cli/pull/1621)).

## Fixes

- `vcf variantqc` indexes a bgzipped VCF before running DISCVRSeq, or works
  from an indexed copy when the VCF is plain text or its folder is read-only.
  DISCVRSeq stopped with "An index is required but was not found" on an
  unindexed `.vcf.gz` ([#1621, VariantQC and Tools labels](https://github.com/WGSExtract/wgsextract-cli/pull/1621)).
