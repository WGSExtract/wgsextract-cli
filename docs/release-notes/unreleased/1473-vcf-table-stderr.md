## Fixes

- `api.vcf.table()` now closes its subprocess pipes on success and failure,
  avoiding Python's unclosed-file warning
  ([#1473, VCF table pipe leak](https://github.com/WGSExtract/wgsextract-cli/issues/1473)).
