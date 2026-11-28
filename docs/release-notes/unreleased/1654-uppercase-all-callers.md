## Fixes

- Clair3, DeepVariant, GATK, FreeBayes, and structural variant callers now
  write uppercase sequence alleles on soft-masked references. Symbolic alleles,
  breakend contig names, and gVCF reference block boundaries are preserved
  ([#1654, lowercase caller alleles](https://github.com/WGSExtract/wgsextract-cli/issues/1654)).
  Rerunning a completed DeepVariant job from an older version regenerates
  its outputs once.
