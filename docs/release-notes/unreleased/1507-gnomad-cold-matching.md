## Changed

- `vcf gnomad` builds its normalized allele lookup in fewer passes on a cache
  miss, without changing how alleles are matched.
