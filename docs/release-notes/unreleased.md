<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `qc ancestry` estimates admixture-style ancestry proportions from a BAM/CRAM,
  VCF or microarray raw-data file: 7 continental and 12 sub-continental
  reference components learned from the HGDP and 1000 Genomes samples in
  gnomAD, each with a 95% interval. They are proportions of reference
  populations, not an ethnicity. The personal report shows them. The 16 MB
  reference is downloaded on first use (`ref ancestry --install` fetches it
  ahead). See [docs/ancestry.md](../ancestry.md).
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

- `wgsextract ref dense-sa` builds `wgse-bwa`'s optional dense suffix-array
  file for a reference (0.80 GB for hs38; `--list` and `--delete` too), and
  `align`/`realign` read it with `--dense-suffix-array` or
  `dense_suffix_array = true` in `config.toml`: the same alignments, about 11%
  faster on the CPU and 8% on the GPU, with the file held in RAM. It is never
  used unless asked; otherwise runs pass `wgse-bwa --dense-sa off`. Needs
  `wgse-bwa` 0.11.0 or later. See docs/hardware.md.

- `vcf snp` and `vcf indel` are about a fifth faster on Apple Silicon Macs
  with `wgse-mimalloc`, WGS Extract's build of the mimalloc allocator, which
  is now included in the default Pixi environment on Apple Silicon (published
  as [wgse-mimalloc-1](https://github.com/WGSExtract/wgsextract-cli/releases/tag/wgse-mimalloc-1),
  pinned by URL and SHA-256). They inject it into bcftools: the same records
  (5M read pairs: 102 against 132 s for SNPs, 155 against 193 s for indels). The new
  `mimalloc` setting and `--mimalloc off` turn it off; the manifest's
  `bcftools_allocator` metric says which allocator ran. See
  [hardware.md](../hardware.md#bcftools-with-mimalloc).

## Fixes

- Runs stopped with Ctrl-C or SIGTERM report `cancelled`, rather than `failed`,
  when a cleanup failure replaces the stop-signal handler's exit (#717).
