## Changed

- `vcf acmg` uses a local indexed sweep for densely spaced sites. Sparse and
  remote lookups still use the range reader.
