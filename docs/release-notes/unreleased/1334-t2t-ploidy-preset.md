## Fixes

- Every command that calls variants from a BAM or CRAM now follows one ploidy
  policy: `--ploidy` or `--ploidy-file`, else the reference library's ploidy
  file, else bcftools' GRCh37 or GRCh38 preset. A T2T or unrecognised build,
  whose sex-chromosome PARs no preset fits, is refused with the fix (the
  command's ploidy option, or `ploidy_<build>.txt` in the reference library)
  instead of borrowing another build's preset. `microarray` on an
  unrecognised build no longer calls every chromosome diploid, and `vcf trio`,
  GATK, Clair3 and DeepVariant calling and `benchmark` share the same refusal
  ([#1334, T2T ploidy preset](https://github.com/WGSExtract/wgsextract-cli/issues/1334)).
