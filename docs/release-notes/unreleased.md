<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

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

## Fixes
