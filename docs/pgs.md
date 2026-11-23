# Local polygenic scores (research only)

**Research use only; not a diagnosis.** A polygenic score is a weighted sum
of alleles, not an absolute disease risk. Do not change treatment or screening
based on it. Clinical disease scores carry this warning too.

```bash
wgsextract score pgs --input sample.vcf.gz --build GRCh38 --trait height --download
wgsextract score pgs --input sample.vcf.gz --build GRCh38 --pgs-id PGS000018 --download --json
```

Genotypes stay on your machine. Only with `--download` is the score ID/build sent to the
PGS Catalog, to download its metadata and harmonized weights. Without it, cached
files are reused and missing public data causes an error naming `--download`.
Explicit download permission also permits refreshing cached Catalog files.
See [privacy](privacy.md) for destinations and persistent configuration. No scoring
files are bundled or mirrored. Downloads retry transient failures three times.
Each attempt checks the advertised byte count (when present) and validates
the complete gzip stream or metadata JSON before replacing a cached file.
Failed or truncated responses never replace a previously good copy.
`bcftools` must already be on PATH; the command checks, it never installs tools.

## Choosing a score

| `--trait` | PGS ID | Catalog trait |
|---|---|---|
| `cad` | PGS000018 | Coronary artery disease |
| `breast-cancer` | PGS000004 | Breast cancer |
| `height` | PGS000297 | Height |
| `t2d` | PGS000020 | Type 2 diabetes |
| `bmi` | PGS005198 | Body mass index (BMI) |
| `stroke` | PGS000038 | Stroke |
| `triglycerides` | PGS000063 | Triglycerides |
| `ldl-cholesterol` | PGS000065 | LDL cholesterol |
| `prostate-cancer` | PGS000067 | Prostate cancer |
| `lung-cancer` | PGS000070 | Lung cancer |
| `thyroid-cancer` | PGS000087 | Thyroid cancer |

This is a convenience list, not a clinical recommendation or a ranking of
predictive accuracy. Any `PGSnnnnnn` is accepted with `--pgs-id`; an uncurated
ID emits `pgs_uncurated_score`. A Catalog score whose authors set their own
licence emits `pgs_license_restricted` with it, unless the licence only asks
for attribution (CC BY 4.0). The `bmi` score, PGS005198, is the GIANT
consortium's multi-ancestry score (Smit et al. 2025, Nat Med), under CC BY 4.0.
It replaced PGS000027 (Khera et al. 2019), which is only "freely available to
the academic community for research use"
([#1165](https://github.com/WGSExtract/wgsextract-cli/issues/1165) compares the
candidates). Review the publication, development ancestry,
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
- An explicit diploid genotype can match. Missing records, `./.`, site- or
  sample-filtered (`FILTER` or `FORMAT/FT`) calls, unresolved alleles and
  ambiguous duplicate records are unmatched.
  **Missing never means homozygous reference by default**, even in WGS.
- Reference evidence: given a gVCF, or a variant VCF whose caller published
  a gVCF beside it (`--reference-evidence gvcf`, found through the caller's
  result manifest), a site is homozygous reference only where the gVCF
  calls it callable reference: enough depth and, where the gVCF has GQ,
  genotype quality (the `confidence_*` settings). Sites the gVCF calls
  `uncovered`, `low_quality_reference`, `filtered`, `no_call`, `variant`,
  `excluded_by_policy` or `not_assessed` stay missing, counted under that
  name. Sites the VCF doesn't list need an indexed `--ref` to check their
  alleles; `--ref` also gives the length of a contig the gVCF declares
  without one (otherwise its sites are `not_assessed`). See [reference-evidence.md](reference-evidence.md).
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
The input stem is the same for `.vcf`, `.vcf.gz`, `.vcf.bgz` and `.bcf`;
compression and variant-file extensions are not part of the output name.
The JSON and manifest details include score sum, matched/total counts,
match rate and reason counts, build, sample, scoring-file SHA-256, licence,
citation and research warning. `--json` prints the usual result-manifest
document for scripts; the typed API is `api.score.pgs(api.ScorePgsParams(...))`.

### Ancestry-adjusted percentiles

For the curated scores on **GRCh38**, `score pgs` also places the score
against the HGDP+1KG reference samples, adjusted for the sample's genetic
ancestry, the way the PGS Catalog Calculator (`pgsc_calc`, its
`pgscatalog.calc` 0.3.1 ancestry step) does:

1. **The reference.** The unrelated HGDP and 1000 Genomes samples the
   [`qc ancestry`](ancestry.md) PCA was trained on (gnomAD v3.1 HGDP+1KG, CC0)
   were scored with the same variant rules as your sample, on pgsc_calc's own
   HGDP+1kGP reference genotypes. That panel lacks 149 of the 3,129 samples,
   all 28 Oceanians among them (gnomAD's sample QC), so 2,980 remain: AFR 605,
   AMR 361, CSA 603, EAS 667, EUR 607, MID 137. There is no Oceanian
   group. Only the scoring-file rows the reference has (the *calibration set*) count toward the calibrated score; the
   published reference file holds those rows, their reference allele
   frequencies, the reference samples' scores and the fitted models, not
   genotypes or scoring files. It is the GitHub release `pgs-ref-2`
   (30 MB), downloaded into `<library>/pgs/` only with `--download`,
   and checked against a pinned SHA-256. Each score is
   tied to the SHA-256 of the Catalog file it was built from; if the Catalog
   changes that file, or you supply your own, there is no percentile.
2. **Your ancestry.** Your genotypes at the ~187,000 ancestry sites are
   projected onto the reference's 10 principal components (the `qc ancestry`
   PCA; `qc ancestry` reports the same `ancestry_pc1`…`ancestry_pc10`).
   Site- or sample-filtered calls are left out. With reference evidence (a
   gVCF input, or the gVCF the VCF's caller published), an absent site is
   homozygous reference only where the gVCF calls it callable reference;
   `ancestry_calibration.pca_missing_site_states` counts the absent sites'
   states. Without it, a whole-genome variants-only VCF's absent sites count
   as homozygous reference. Either way, only inside `--confident-bed` when
   one is given.
3. **The adjustment** (`pgscatalog.calc`'s methods, reimplemented):
   - **Adjusted percentile** (headline): `Z_norm2`, your score minus a mean
     and divided by an SD that both depend on PCs 1-4, fitted jointly on the
     reference (Khan et al. 2022, Nat Med; eMERGE GIRA), and its normal
     percentile.
   - `z_norm1`: only the mean adjusted (Khera et al. 2019, Circulation).
   - **Empirical percentile**: your score's percentile among the reference
     samples of the region you are most similar to (Mahalanobis distance on
     PCs 1-5: AFR, AMR, CSA, EAS, EUR or MID), when that match isn't
     low-confidence (p < 1e-10).

Calibration rows your VCF has no genotype for are filled in with the mean
dosage of the reference region you are most similar to, as pgsc_calc fills in
missing genotypes with reference frequencies. The error this adds
(`imputation_error_sd`, in units of `Z_norm2`'s fitted SD at your PCs) is the
imputed rows' variance under that region's frequencies, inflated by the
score's linkage disequilibrium there (the reference samples' score variance
over the summed per-variant variance). Assuming the imputed rows share the
whole score's LD makes it an upper bound for scores of many correlated
variants: on real HG002 and HG005 gaps it is 0.06-0.18 SD where the measured
error is 0.05-0.16 SD, and without the inflation it would be half the
measured error for PGS000027 (version 1 of the reference, #617).
`percentile_interval` is the resulting 95% interval. The
percentile is **withheld** (`percentile_status`, and a `pgs_uncalibrated`
warning saying why) when:

| `percentile_status` | Meaning |
| :--- | :--- |
| `calibrated` | reported (not withheld) |
| `below_match_threshold` | under `--min-match` of the scoring rows matched |
| `reference_calibration_unavailable` | not a curated Catalog file on GRCh38 (custom files, uncurated IDs, GRCh37) |
| `reference_unavailable` | a curated file, but the HGDP+1KG reference couldn't be downloaded or verified (the reason says why) |
| `calibration_coverage_too_low` | more than 5% of the calibrated score's variance would be filled in |
| `ancestry_projection_unavailable` | the VCF can't give ancestry genotypes (variants-only, not a whole genome, and without reference evidence) |
| `ancestry_projection_incomplete` | under 90% of the ancestry sites have a genotype |
| `ancestry_outside_reference` | your PCs are outside the reference samples' range (p < 1e-10) |

When it is reported, `confidence` is `medium`, or `low` with a
`pgs_ancestry_mismatch` warning when the score was developed in ancestries
other than the reference region you are most similar to. The development
ancestry is pinned with each calibrated scoring file, so it holds for a
`--scoring-file` copy without the Catalog's metadata. It is never
`high`: a percentile says where your score falls among reference genomes,
not your risk, and a score predicts less well outside the ancestry it was
developed in, whatever the adjustment. At less than `--min-match` (default
0.75), `pgs_low_match` also warns, and confidence is `low`.

The JSON's `ancestry_calibration` holds the calibrated score, calibration
counts and imputed variance share, the PCs, the most similar population and
its similarity, the empirical percentile and Z, `z_norm1`, `z_norm2`,
`imputation_error_sd` and `percentile_interval`. The
manifest's metrics add `percentile` and `z_adjusted`; the TSV adds
`z_adjusted` and `percentile_status`.

**Validation** (#617, on lin-big, 2026-11-14, for version 1's 11 scores; version 2
copies ten of them unchanged):

- **Same rules as your sample.** For 3 reference samples and all 11 scores,
  `score pgs`'s own scorer, run on their genotypes exported as a VCF, gives
  the reference sums to 2.7e-6 (plink2's printed precision).
- **Same adjustment as pgscatalog.calc 0.3.1.** Given the same reference,
  PCs and scores, `compare_ancestry` (Mahalanobis) and `pgs_adjust` agree on
  the most similar region for every one of the 962 reference samples outside
  the training set and the GIAB samples, on Z_norm1 and Z_norm2 within 0.0024,
  and on the empirical percentile exactly (apart from ties within rounding).
  pgscatalog.calc's default RandomForest picks the same region for 97%.
- **Calibrated out of sample.** Across those 962 samples, Z_norm2 has mean
  within ±0.3 and SD 0.82-1.10 in each region (MID: 21 samples), and 92-99%
  fall within ±1.96.
- **GIAB VCFs.** HG001's GIAB v4.2.1 VCF and confident BED give calibrated
  scores within 0.18 SD of its genotypes in the reference panel, as expected
  from its imputed share.

**Version 2** (#1165, on macOS, 2026-11-22) rebuilt only `bmi`'s PGS005198.
The same build reproduces version 1's height sums exactly, and the same
checks hold:

- **Coverage.** 1,202,909 of its 1,217,710 rows (98.8%) are calibration rows.
- **Same rules as your sample.** `score pgs`'s scorer gives the reference
  sums of 3 reference samples to 3.9e-6.
- **Same adjustment as pgscatalog.calc 0.3.1.** It agrees on the most similar
  region for all 962 out-of-training samples, on Z_norm1 and Z_norm2 within
  0.0014, and on HG002 (Z_norm2 -2.302 against -2.303).
- **Calibrated out of sample.** Z_norm2 has mean within ±0.1 and SD
  0.90-1.09 in AFR, AMR, CSA, EAS and EUR, with 92-97% within ±1.96. MID's
  21 samples have mean 0.55 (SD 1.17), and the score's development
  ancestries don't include MID, so such samples also get
  `pgs_ancestry_mismatch`.
