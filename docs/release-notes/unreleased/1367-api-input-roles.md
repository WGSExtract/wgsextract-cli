## Fixes

- The Python API records a command's input files under the same result
  manifest roles and params as the CLI. A VCF given as `vcf_input` (for
  `vcf` operations on a VCF and `qc vcf`) is role `vcf_input` with
  `provenance.params.vcf_input` set, not role `input`; a `vcf trio` proband
  is role `proband`; and `r1`/`r2` reads are `fastq_r1`/`fastq_r2`
  ([#1367, API input roles](https://github.com/WGSExtract/wgsextract-cli/issues/1367)).
