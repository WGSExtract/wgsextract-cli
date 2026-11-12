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
and writes GLIMPSE2's binary reference chunks. The download is deleted;
the SNP panel plus binary references require roughly 70 GB for all autosomes.
It checks for free space of 2.5 times the requested downloads first.

| | Download | Licence |
| --- | --- | --- |
| Panel: 1000 Genomes 30x phased SNV/indel/SV panel (20220422), chr1–chr22 | about 0.7 GB for chr20, 30 GB for all autosomes | IGSR: free to use without restriction |
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
  `INFO/RAF`), Beagle's `INFO/DR2`. Beagle output also keeps the reference
  panel's `AC`/`AN`: Beagle's own `AF` is the target-sample frequency and
  must not be used to decide whether an imputed allele is rare in the panel.
- `FILTER` `ImpLowConf` on imputed genotypes whose most likely genotype has
  `GP` below `--min-gp` (0.9) or whose score is below `--min-info` (GLIMPSE2
  INFO: 0.8; Beagle DR2: 0.04). An explicit `--min-info` overrides either
  default; zero disables the score cutoff, not the GP cutoff.
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

### Phase 0: HG002 chr20 (GO)

GRCh38, GIAB v4.2.1 calls inside its confident BED, the 3,202-sample panel
and 26 GLIMPSE2 chunks. The original 35x BAM was downsampled to each depth.
Beagle 5.5 used HG002 truth genotypes at 14,795 lifted 23andMe v5 chr20 sites;
those input sites were excluded from its evaluation.

| Input | Dosage r², MAF 0.1–0.5% | MAF 1–5% | MAF 5–20% | MAF 20–50% | NRC, MAF >5% (all / PASS) | PASS share, MAF >5% |
|---|---|---|---|---|---|---|
| GLIMPSE2 0.5x | 0.894 | 0.960 | 0.971 | 0.958 | 0.950 / 0.985 | 0.94 |
| GLIMPSE2 1x | 0.918 | 0.974 | 0.985 | 0.979 | 0.974 / 0.992 | 0.97 |
| GLIMPSE2 2x | 0.942 | 0.982 | 0.991 | 0.988 | 0.985 / 0.994 | 0.98 |
| GLIMPSE2 4x | 0.959 | 0.989 | 0.995 | 0.992 | 0.991 / 0.996 | 0.99 |
| GLIMPSE2 8x | 0.972 | 0.992 | 0.997 | 0.997 | 0.996 / 0.998 | 1.00 |
| Beagle, chip sites | 0.827 | 0.926 | 0.942 | 0.934 | 0.921 / 0.973 | 0.25 |

Dosage r² compares DS with truth dosage, grouped by the panel's minor allele
frequency. NRC is genotype concordance at sites where truth or the imputed
call is non-reference. This table uses the original GP ≥0.9 and INFO/DR2 ≥0.8
PASS rule; the Beagle threshold calibration below is separate.

The go/no-go accuracy gate (r² ≥0.9 for MAF >5% at 1x) passed: 0.985 and
0.979 in the two common bins. This is one sample and chromosome, not evidence
of equal accuracy across ancestries, chips or the entire genome. Rare
imputed variants are not suitable as clinical findings.

### Confidence calibration for array inputs

On the same HG002 chr20 simulation, score all **136,208** common (panel MAF
>5%) confident-BED sites not supplied as chip input. Hold max GP ≥0.9 fixed,
scan every distinct DR2 value and choose the threshold retaining the most
sites with PASS non-reference concordance ≥0.97.

| Minimum DR2 | PASS sites | PASS share | PASS NRC |
|---|---:|---:|---:|
| 0 (GP only) | 127,444 | 93.57% | 0.96388 |
| 0.03 | 35,936 | 26.38% | 0.96852 |
| **0.04 (new default)** | **35,080** | **25.75%** | **0.97068** |
| 0.05 | 34,510 | 25.34% | 0.97247 |
| 0.10 | 34,099 | 25.03% | 0.97349 |
| 0.80 (old default) | 34,044 | 24.99% | 0.97345 |

This recovers 1,036 sites (+3.0%) versus the old cutoff, but **does not turn
the 25% PASS share into broad coverage**. Many confident reference genotypes
have DR2 zero in this single-target run; dropping the DR2 screen entirely
raises coverage but misses the requested NRC gate. Report the full curve,
not just the selected point. Calibration is in-sample, on one chip simulation,
not a validated accuracy guarantee for other ancestries or arrays.

GLIMPSE2 retains INFO ≥0.8. For the 143,964 evaluated common sites at 1x,
its curve gives 139,571 PASS sites (96.95%) and NRC 0.99166 at that cutoff;
lowering it does not recover additional GP-eligible calls on this dataset.
The curves and matching script's run logs are in `out/621/dr2/`.

### Resources

Measured on lin-big with four threads:

| Step | chr20 wall time | Peak RSS |
|---|---|---|
| GLIMPSE2 phase + ligate, per depth | 431–464 s | 2.2–3.1 GB |
| Beagle | 60 s | 3.4 GB |
| One-time panel preparation | about 10 min | — |

Extrapolating chr20 (about 2.2% of autosomal bases) gives **about 5.5 hours
for whole-autosome GLIMPSE2 at four threads**, or **2.8 hours at eight**,
with peak RAM near 3 GB. Beagle extrapolates to about 45 minutes. These are
projections, not measured whole-genome runs, and exclude panel preparation.
The original two-hour target at four threads was **not met**; memory fits
a 16 GB machine comfortably. `vcf impute` prints its selected-chromosome
time/RAM estimate before running; use `--threads` to select the CPU budget.

The Phase 0 scripts, per-bin JSON, time.tsv and logs are retained in
`out/621/phase0/` in the validation checkout, with the result summary on
[issue #621](https://github.com/WGSExtract/wgsextract-cli/issues/621).

### Product-command validation

The actual `ref imputation --install --chromosomes chr20` and `vcf impute`
commands were run on lin-big against the same 1x BAM, under an agent-coord
four-core/8 GiB lease (nominal 1x input; product depth estimate 1.15x).
The installer reused and SHA-256-verified the original
download on that host, then prepared its own panel and binary chunks.

| Panel MAF | Phase 0 dosage r² | Product dosage r² |
|---|---:|---:|
| 5–20% | 0.984843836 | 0.984841991 |
| 20–50% | 0.979164025 | 0.979161000 |

The common-bin differences are below 0.0000031, passing the r² ≥0.9 gate
again. The product wrote 1,430,673 labelled sites (9,785 `ImpLowConf`),
with the provenance header and a result manifest. Panel preparation took
7 min 34 s; imputation and labelling took 7 min 3 s, peaking at 2.7 GB RSS.
Evidence (`comparison.json`, GIAB evaluation, header, timings and command
log) is retained in `out/621/product/`. The large output stays on lin-big,
under `/mnt/models/wgse-621/out/621/product/output/`.
