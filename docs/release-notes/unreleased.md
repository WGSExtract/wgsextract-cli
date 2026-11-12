<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `ref library --install` downloads genomes from copies in this project's
  GitHub releases first, checked against a pinned SHA-256, and falls back to
  the original source (NCBI, EBI, UCSC, AWS), which has been slow and flaky.
  See [docs/reference-genomes.md](../reference-genomes.md).
- `--aligner auto` on Apple Silicon can now pick bwa-mem2 when
  `wgse-bwa-mem2` 0.5.0 or later is installed, without upstream bwa-mem2 or
  the reference's `.0123` file, using its smaller memory estimate. bwa-mem2's
  records differ from bwa's, so a run that used to align with bwa (or
  `wgse-bwa`) may now give a different BAM; pass `--aligner bwa` to keep bwa.
- `vcf deepvariant` runs DeepVariant natively on Apple Silicon Macs with
  `wgse-deepvariant`, WGS Extract's build of DeepVariant 1.10.0 (the
  `macos-deepvariant` release): no Docker, call_variants on the GPU, and the
  same sites, genotypes and filters as Google's x86-64 image (HG002 chr20 in
  about 5 minutes on an M1 Pro; 16 GB Macs fit). Put it on `PATH` and set the
  new `deepvariant_weights` setting in `config.toml` to its weights folder,
  whose files are checked against their published hashes; only the WGS model
  is qualified. `deepvariant_native = "off"` keeps the Docker image instead.
  See [native/deepvariant/README.md](../../native/deepvariant/README.md).

## Fixes
