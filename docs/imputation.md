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
`1`, `2`... is renamed for you. Contig lengths must be present to verify
GRCh38; for a lengthless header, supply the matching GRCh38 FASTA index with
`bcftools reheader --fai GRCh38.fa.fai` first.

The optional environment supports **x86-64 Linux, Windows x64 and Intel macOS**.
On Windows, `pixi install -e imputation` installs our native GLIMPSE2 2.0.1
package from the `glimpse2-1` release, pinned by URL and SHA-256 in `pixi.toml`,
alongside conda-forge's native OpenJDK and the pinned noarch Beagle jar.
No MSYS2 or WSL runtime is needed; GLIMPSE2 phase requires an AVX2/FMA-capable
CPU. Windows CI tests a fresh install and both tools on the real HG002 fixture
without skips. Missing tools fail dependency validation; neither feature command
installs tools. **Windows ARM64, Linux ARM64 and Apple Silicon remain gated off.**
The server must allow the effective reference library and panel paths,
not just the FASTA's `genomes/` subfolder; configured defaults and symlinks are
checked too. Failed or version-mismatched binary-chunk builds also recheck
disk space on retry (about 1.6 times the download size for pending chunks).

## Output and labelling

`imputed.vcf.gz` (+ `.tbi`) holds every panel site of the chromosomes imputed:

- `INFO/IMP` marks an imputed genotype. GLIMPSE2's are all imputed; Beagle
  leaves the chip's observed genotypes unmarked. Missing chip genotypes are
  removed before Beagle, so their filled-in calls are marked `IMP` too.
  Previously imputed source calls are also removed rather than promoted to
  observed chip genotypes.
- `FORMAT/GT`, `DS` (dosage) and `GP` (genotype probabilities), and the
  site's INFO score: GLIMPSE2's `INFO/INFO` (with the panel frequency
  `INFO/RAF`), Beagle's `INFO/DR2`. Beagle output also keeps the reference
  panel's counts as `WGSE_PANEL_AC`/`WGSE_PANEL_AN`. These dedicated fields
  survive sample subsetting; standard `AC`/`AN` can be recalculated from one
  sample. Beagle's own `AF` is also a target-sample frequency. Neither
  unqualified `AF` nor ordinary `AC`/`AN` establishes panel rarity.
- `FILTER` `ImpLowConf` on imputed genotypes whose most likely genotype has
  `GP` below `--min-gp` (GLIMPSE2: 0.9; Beagle: 0.95). There is **no default
  INFO or DR2 filter**. `--min-info` remains an explicit opt-in site filter,
  meaningful only for multi-sample cohorts, not single-target imputation.
- A header line `##wgsextract_imputation=<Tool=...,Version=...,Panel=...>`.

Downstream commands use these labels:

- `qc vcf` warns that the file is imputed (`imputed_vcf`).
- `vcf table` has an `imputed` column and the genotype's probability
  ([variant-table.md](variant-table.md)).
- Confidence labels ([variant-confidence.md](variant-confidence.md)) make
  every imputed call `low` (reason `imputed`), and an imputed allele rarer
  than 1% in the panel is never listed as a ClinVar or rare-variant finding.
- `microarray` exports `ImpLowConf` genotypes as no-calls, not as the
  reference. Targets absent from an imputed VCF are also no-calls: the panel
  did not assess them, so they cannot be filled as homozygous reference.

## Accuracy, time and memory

### Phase 0: HG002 chr20 (GO)

GRCh38, GIAB v4.2.1 calls inside its confident BED, the 3,202-sample panel
and 26 GLIMPSE2 chunks. The original 35x BAM was downsampled to each depth.
Beagle 5.5 used HG002 truth genotypes at 14,795 lifted 23andMe v5 chr20 sites;
those input sites were excluded from its evaluation.

| Input | Dosage r², MAF 0.1–0.5% | MAF 1–5% | MAF 5–20% | MAF 20–50% | NRC, MAF >5% (all calls) |
|---|---|---|---|---|---|
| GLIMPSE2 0.5x | 0.894 | 0.960 | 0.971 | 0.958 | 0.950 |
| GLIMPSE2 1x | 0.918 | 0.974 | 0.985 | 0.979 | 0.974 |
| GLIMPSE2 2x | 0.942 | 0.982 | 0.991 | 0.988 | 0.985 |
| GLIMPSE2 4x | 0.959 | 0.989 | 0.995 | 0.992 | 0.991 |
| GLIMPSE2 8x | 0.972 | 0.992 | 0.997 | 0.997 | 0.996 |
| Beagle, chip sites | 0.827 | 0.926 | 0.942 | 0.934 | 0.921 |

Dosage r² compares DS with truth dosage, grouped by the panel's minor allele
frequency. NRC is genotype concordance at sites where truth or the imputed
call is non-reference. These are unfiltered accuracy figures. The corrected
single-target GP filtering is evaluated separately below.

The go/no-go accuracy gate (r² ≥0.9 for MAF >5% at 1x) passed: 0.985 and
0.979 in the two common bins. This is one sample and chromosome, not evidence
of equal accuracy across ancestries, chips or the entire genome. Rare
imputed variants are not suitable as clinical findings.

### Per-genotype confidence for single-target imputation

The command takes exactly one target sample. Beagle's DR2 measures dosage
spread across target haplotypes: with one sample it acts largely as a
zygosity indicator. Confident hom-ref **and hom-alt** calls can have DR2 zero.
Any positive default DR2 cutoff therefore discards genuine homozygous calls
and turns them into chip-export no-calls. Do not use it as genotype confidence.
GLIMPSE INFO is also a site statistic, not a substitute for the target's GP.
Both scores are retained in the output, but neither is filtered by default.

Use the per-genotype max GP instead: **Beagle defaults to 0.95; GLIMPSE2 to
0.9**. The GP curves score common (panel MAF >5%) sites inside GIAB's confident
BED, excluding Beagle's chip-input positions. They are retained in
`out/621/gp/`; the earlier DR2 calibration is superseded.

| Tool | Minimum max GP | Common PASS sites | PASS share | PASS NRC |
|---|---:|---:|---:|---:|
| Beagle | 0.90 | 127,444 / 136,208 | 93.57% | 0.96388 |
| Beagle | **0.95 (default)** | **123,445 / 136,208** | **90.63%** | **0.97367** |
| Beagle | 0.99 | 115,776 / 136,208 | 85.00% | 0.98338 |
| GLIMPSE2 1x | **0.90 (default)** | **139,571 / 143,964** | **96.95%** | **0.99166** |
| GLIMPSE2 1x | 0.95 | 137,677 / 143,964 | 95.63% | 0.99454 |
| GLIMPSE2 1x | 0.99 | 132,884 / 143,964 | 92.30% | 0.99725 |

Beagle GP ≥0.95 meets the aggregate common-site NRC ≥0.97 gate while preserving
confident homozygous calls. In these evaluated sites, 16,813 of 18,489
GP-qualified hom-alt calls have DR2 zero; they must not be discarded for that.
GLIMPSE2 GP ≥0.9 already clears the NRC gate with broad coverage; increasing
it is an optional precision/coverage trade-off, not required by these data.

Calibration is on one HG002 chip simulation/chromosome, not an independent
accuracy guarantee for other ancestries or arrays.

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

The corrected **Beagle** product command was also run end to end on chr20,
with the same chip VCF and product panel, under a four-core/
8 GiB lease. Its dosage r² exactly matches Phase 0 in every MAF bin, including
0.941519598 (5–20%) and 0.933948882 (20–50%).

The actual output `FILTER` values, not just a reconstructed threshold, give
123,445 / 136,208 common sites PASS (**90.63%**) and aggregate NRC
**0.973671875**. All **18,489 / 18,489** GP-qualified hom-alt calls in these
evaluated sites are PASS; there are zero disagreements between the output
filter and the GP-only rule. The header records `MinGP=0.95` without a
`MinINFO` condition, and the manifest reports the site filter as disabled.

The product wrote 1,416,853 imputed sites (27,740 `ImpLowConf`), took
2 min 34 s including input preparation/labelling, and peaked at 2.2 GB RSS.
Evidence is in `out/621/product-beagle/`; its large VCF remains at
`/mnt/models/wgse-621/out/621/product-beagle/output/`.

The no-call fixture probe at chr20:10,008,603 is filled as `1|1`, GP=1,
DR2=0, **PASS and IMP**, and its exported table row has `imputed=1`.
It is a direct regression for both the homozygote filtering and missing-probe
labelling bugs. Panel-rarity suppression remains in force for imputed calls.
