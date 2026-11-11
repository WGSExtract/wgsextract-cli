# Genotype imputation (`vcf impute`)

`wgsextract vcf impute` fills in a GRCh38 sample's genotypes at the sites of
the 1000 Genomes 30x reference panel (3,202 people, NYGC, phased):

- **Low-coverage reads** (a BAM or CRAM at about 0.5–8x) with
  [GLIMPSE2](https://github.com/odelaneau/GLIMPSE) (Rubinacci et al. 2023,
  *Nature Genetics*; MIT). GLIMPSE2 reads the reads at every panel site and
  combines what little they show with the haplotypes of the panel.
- **Genotype-only VCFs**, such as microarray calls, with
  [Beagle 5](https://faculty.washington.edu/browning/beagle/beagle.html)
  (Browning et al. 2021; GPL-3, run as a separate program).

```sh
pixi install -e imputation                                    # once: GLIMPSE2, Beagle, Java
wgsextract ref imputation --install --chromosomes chr20      # once per chromosome
wgsextract vcf impute --input lowpass.cram --chromosomes chr20
wgsextract vcf impute --vcf-input chip.vcf.gz
```

Above about 8x, call the reads directly (`vcf snp`, `vcf deepvariant`):
imputation adds little there and can only find variants the panel holds.
`vcf impute` warns (`high_depth_imputation`) when the reads are deeper.

## The reference panel

`ref imputation --install --chromosomes chr20` (or `chr1-22`, or `all`)
downloads each chromosome's panel VCF from IGSR and GLIMPSE's genetic map,
checks both against pinned SHA-256 digests, keeps the panel's biallelic SNPs
and writes GLIMPSE2's binary reference chunks. The download is deleted; what
stays is about the size of the download. It checks the free space first.

| | Download | Licence |
| --- | --- | --- |
| Panel: 1000 Genomes 30x phased SNV/indel/SV panel (20220422), chr1–chr22 | about 1 GB for chr20, 32 GB for all autosomes | IGSR: free to use without restriction |
| Genetic maps: GLIMPSE's GRCh38 maps (pinned to commit `6fe2552`) | under 1 MB each | MIT |

The panel is GRCh38 only, with contigs named `chr1`, `chr2`...; chrX needs
each sample's ploidy and isn't supported yet. A chip VCF with contigs named
`1`, `2`... is renamed for you.

## Output and labelling

`imputed.vcf.gz` (+ `.tbi`) holds every panel site of the chromosomes imputed:

- `INFO/IMP` marks an imputed genotype. GLIMPSE2's are all imputed; Beagle
  leaves the chip's own genotypes unmarked.
- `FORMAT/GT`, `DS` (dosage) and `GP` (genotype probabilities), and the
  site's INFO score: GLIMPSE2's `INFO/INFO` (with the panel frequency
  `INFO/RAF`), Beagle's `INFO/DR2`.
- `FILTER` `ImpLowConf` on imputed genotypes whose most likely genotype has
  `GP` below `--min-gp` (0.9) or whose INFO score is below `--min-info` (0.8).
- A header line `##wgsextract_imputation=<Tool=...,Version=...,Panel=...>`.

Downstream commands use these labels:

- `qc vcf` warns that the file is imputed (`imputed_vcf`).
- `vcf table` has an `imputed` column and the genotype's probability
  ([variant-table.md](variant-table.md)).
- Confidence labels ([variant-confidence.md](variant-confidence.md)) make
  every imputed call `low` (reason `imputed`), and an imputed allele rarer
  than 1% in the panel is never listed as a ClinVar or rare-variant finding.
- `microarray` exports `ImpLowConf` genotypes as no-calls, not as the
  reference.

## Accuracy, time and memory

PHASE0_RESULTS
