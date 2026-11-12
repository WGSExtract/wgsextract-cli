# Local polygenic scores (research only)

**Research use only; not a diagnosis.** A polygenic score is a weighted sum
of alleles, not an absolute disease risk. Do not change treatment or screening
based on it. Clinical disease scores carry this warning too.

```bash
wgsextract score pgs --input sample.vcf.gz --build GRCh38 --trait height
wgsextract score pgs --input sample.vcf.gz --build GRCh38 --pgs-id PGS000018 --json
```

Genotypes stay on your machine. Only the score ID/build is sent to the
PGS Catalog, to download its metadata and harmonized weights. No scoring
files are bundled or mirrored. Downloads retry transient failures three times.
`bcftools` must already be on PATH; the command checks, it never installs tools.

## Choosing a score

| `--trait` | PGS ID | Catalog trait |
|---|---|---|
| `cad` | PGS000018 | Coronary artery disease |
| `breast-cancer` | PGS000004 | Breast cancer |
| `height` | PGS000297 | Height |
| `t2d` | PGS000020 | Type 2 diabetes |
| `bmi` | PGS000027 | Body mass index |
| `stroke` | PGS000038 | Stroke |
| `triglycerides` | PGS000063 | Triglycerides |
| `ldl-cholesterol` | PGS000065 | LDL cholesterol |
| `prostate-cancer` | PGS000067 | Prostate cancer |
| `lung-cancer` | PGS000070 | Lung cancer |
| `thyroid-cancer` | PGS000087 | Thyroid cancer |

This is a convenience list, not a clinical recommendation or a ranking of
predictive accuracy. Any `PGSnnnnnn` is accepted with `--pgs-id`; an uncurated
ID emits `pgs_uncurated_score`. Review the publication, development ancestry,
licence and limitations before using a score. A height score in raw score
units is **not** height in centimetres; a disease score is **not** disease
probability.

Your own additive scoring TSV (optionally gzipped) overrides Catalog selection:

```bash
wgsextract score pgs --input sample.vcf.gz --build GRCh38 \
  --scoring-file my_score.txt --min-match 0.9 --outdir scores
```

Persistent defaults go in `config.toml`:

```toml
pgs_min_match = 0.75
# pgs_keep_ambiguous = false
# pgs_scoring_file = "/path/to/my_score.txt.gz"
```

An explicit `--scoring-file` overrides the configured file. Custom files need
`#genome_build=GRCh38` (or GRCh37), and columns `chr_name`, `chr_position`
(1-based), `effect_allele`, `other_allele`, `effect_weight`. Catalog harmonized
files instead use `hm_chr`, `hm_pos`, and `#HmPOS_build=...`; the harmonized
build takes precedence over their original build. Include `#license=...`,
`#citation=...`, and `#trait_reported=...` in custom files. Unknown licence
or citation is reported explicitly, never guessed.

## Inputs and matching

- Choose the **same build** as the VCF. Only GRCh37 and GRCh38 are supported.
  Known chromosome-1 lengths inconsistent with `--build` are refused.
- Select `--sample NAME` for a multi-sample VCF.
- An explicit diploid genotype can match. Missing records, `./.`, filtered
  calls, unresolved alleles and ambiguous duplicate records are unmatched.
  **Missing never means homozygous reference by default**, even in WGS.
- A called, passing gVCF reference block can supply a reference genotype
  within its bounds when an indexed `--ref` FASTA is supplied.
- For a variants-only callset with a same-sample, same-build confident BED,
  add `--confident-bed sample.confident.bed --ref reference.fa.gz`. Only
  positions inside the BED may be inferred reference; positions outside it
  are unmatched. Explicit no-calls, filtered calls and overlapping deletions
  are never rescued by the BED. The BED is an assertion of callability, not
  just a list of capture targets.
- The reference FASTA needs an existing `.fai` (also `.gzi` for BGZF);
  `samtools faidx reference.fa.gz` creates these. No reference is downloaded
  implicitly.
- This first version scores **additive autosomal SNVs only**, on the forward
  strand, with exact allele matching. Indels, sex chromosomes, unresolved
  harmonization and multi-allelic effect definitions count as unmatched.
  Non-additive, haplotype and dosage-specific scoring models are refused.
  Palindromic A/T and C/G SNPs are also unmatched by default, as in
  `pgsc_calc`: coordinate harmonization alone cannot determine their strand.
  `--keep-ambiguous` (or `pgs_keep_ambiguous = true`) is an expert override
  **only** after independently verifying forward-strand scoring alleles; it
  emits a structured warning. `--no-keep-ambiguous` overrides that setting.
  There is no strand guessing, liftover, normalization, imputation or
  mean-imputation. Arrays and low-pass inputs are not qualified.

The native engine sums `effect_weight × observed effect-allele count` using
NumPy in bounded batches. An on-disk genotype index keeps memory bounded
even for large VCFs. Every scoring-file row counts in the match-rate
denominator, including unsupported or unmapped rows. No matched variants
produces a null score rather than a misleading zero.

`--plink2-check` optionally validates the **same matched calls' arithmetic**
with an already-installed `plink2`, without mean-imputation. It is not an
independent check of the matching policy. The JSON includes the PLINK sum,
difference and text-rounding bound: PLINK's public score report uses six
significant digits, so comparison allows that rounding plus `1e-6`.

## Outputs and percentiles

The command writes `<input>_<PGS ID>_pgs.json`, `.tsv`, and a result manifest.
The JSON and manifest details include score sum, matched/total counts,
match rate and reason counts, build, sample, scoring-file SHA-256, licence,
citation and research warning. `--json` prints the usual result-manifest
document for scripts; the typed API is `api.score.pgs(api.ScorePgsParams(...))`.

**Percentile unavailable: needs a score-specific reference distribution.**
`percentile` is always null in this release. At less than `--min-match`
(default 0.75), status is `below_match_threshold` and `pgs_low_match` warns
that a percentile is refused. Otherwise status is
`reference_calibration_unavailable`. Confidence is `low` or `uncalibrated`,
never a claim of clinical validity.

The HGDP+1KG reference used by `qc ancestry` already contains reusable PCA
loadings and training PCs. It does **not** contain the reference individuals'
genotypes at arbitrary scoring sites or their score distributions. Building
those distributions, scoring the same matched variants, ancestry adjustment
and report integration are Phase 2 of issue #617, not an approximation made
from ancestry proportions or a published population mean.
