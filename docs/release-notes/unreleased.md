<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `vcf gatk --pair-hmm-neon JAR` (opt-in, Apple Silicon) runs GATK
  HaplotypeCaller's PairHMM with GKL's NEON kernels in double precision
  instead of GATK's Java fallback: about 9x faster over five HG002 chr20
  windows, with identical sites, alleles, GT and FILTER. A few reads near the
  informative cutoff are counted differently in difficult regions (9 of
  306,024 reference blocks, 8 final records), so it is not the default. Build
  the sidecar jar as `packaging/arm64/gkl/README.md` describes (#1117).
- Native `wgse-deepvariant` 0.4.1 optimizes Apple Silicon small-model CPU
  work and realigner CIGAR parsing without changing inference or defaults.
  On HG002 35x whole genome, M1 Pro (32 GiB), ten workers, batch 1024 with
  gVCF, measured wall time fell 8,831.4 → 7,984.3 s (9.6%) and CPU time
  50,708.6 → 46,455.5 s (8.4%). All 7,709,239 VCF and 72,210,019 gVCF
  non-header records, including 64,500,780 reference blocks, are exactly
  equal to 0.4.0 by full-body SHA-256. Peak RSS grew 11.95 → 13.32 GB
  (about 1.4 GB); sampled temporary space was 29.4 → 29.5 GB, with zero
  swap growth. One run each, M1 Pro only; 16 GB base-M1 qualification
  remains follow-up. Requires installing the separately published native
  0.4.1 binary on PATH; updating the CLI alone does not replace it
  ([#1116, Apple Silicon performance](https://github.com/WGSExtract/wgsextract-cli/issues/1116)).
- Native `wgse-deepvariant` 0.4.0 supports haploid chrX/Y/MT with diploid
  PARs, including gVCF reference confidence. It matches Google's haploid
  likelihood adjustment while retaining diploid GT/PL encoding. Empty-read
  partitions now retain zero-depth reference evidence instead of leaving gVCF
  gaps. Older native builds still refuse non-diploid policies (#1103).
- `vcf clair3 --gpu` runs Clair3's networks on the Apple Silicon GPU (MPS)
  through the code-only `wgse-clair3` package, which `pixi install -e clair3`
  now installs on Apple Silicon. It keeps Clair3's CPU streaming pipeline and
  gVCF; on a 1 Mb HG002 ONT slice its calls and gVCF reference blocks match the
  CPU run. CPU stays the default, and no model weights are redistributed
  (#1101).

## Changed

- Annotation data that `ref` commands download (ClinVar, CADD, gnomAD,
  PhyloP, GERP, gene maps, PharmGKB, SpliceAI, ACMG data, the HLA index) now
  goes in the reference library's `downloads/` folder instead of `ref/`.
  Libraries that already have data in `ref/` keep working without moving it,
  and `ref bootstrap` removes the empty `ref/` its archive leaves behind.

## Fixes

- `lineage y-haplogroup --tree ftdna` and `--tree isogg` correct independently
  verified ancestral/derived reversals using a pinned, separately licensed
  YBrowse polarity module. This fixes inverted backbone evidence and wrong
  haplogroup calls, including reverse-strand T2T markers. Ancient panels are
  filtered after correction. Version 2 requires compatible source branches
  and reference-lineage agreement; name-only matching in the superseded
  version 1 incorrectly flipped valid sister-branch markers. Use version 2,
  not version 1. First use requires `--download`; custom marker
  panels are unchanged ([#1168, Y marker polarity](https://github.com/WGSExtract/wgsextract-cli/issues/1168)).
