<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- Genotype imputation now works natively on **Windows x64**:
  `pixi install -e imputation` installs GLIMPSE2 2.0.1 from our `glimpse2-1`
  release (pinned by URL and SHA-256), Beagle and native OpenJDK.
  `ref imputation` prepares the GRCh38 panel; `vcf impute` imputes
  low-coverage reads or genotype-only VCFs with downstream imputed-call labels.
  No MSYS2 or WSL is needed at runtime. GLIMPSE2 phase requires AVX2/FMA;
  Windows ARM64, Linux ARM64 and Apple Silicon remain unsupported.
  See [imputation.md](../imputation.md).

- `score pgs` computes research-only local polygenic score sums from a VCF,
  with 11 curated PGS Catalog IDs, arbitrary IDs (with a warning), and custom
  scoring files. JSON, TSV and the manifest report missingness, licence and
  citation. Absent variants are never assumed reference without a confident
  BED or called gVCF block and indexed FASTA. Percentiles remain unavailable
  pending score-specific reference calibration. See [pgs.md](../pgs.md).

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
  `macos-deepvariant` release, 0.1.1 or later): no Docker, call_variants on
  the GPU, and the same PASS calls, genotypes and filters as Google's x86-64
  image (HG002 chr20 in about 5 minutes on an M1 Pro; HG003's whole genome in
  2 h 42 min with every hap.py count identical to Google's and two QUAL-0
  RefCalls differing; 16 GB Macs fit). Put it on `PATH` and set the
  new `deepvariant_weights` setting in `config.toml` to its weights folder,
  whose files are checked against their published hashes; only the WGS model
  is qualified. `deepvariant_native = "off"` keeps the Docker image instead.
  See [native/deepvariant/README.md](../../native/deepvariant/README.md).
- `vcf deepvariant` also runs `wgse-deepvariant` on Linux x86-64 (0.2.0, the
  new `linux-deepvariant` conda packages): DeepVariant 1.10.0's WGS model
  without Docker, on the CPU, or with `--gpu` on an NVIDIA GPU through CUDA
  instead of Parabricks (call_variants for HG002 chr20 in 20 s on an RTX 3090,
  against 79 s for Google's GPU image and 15 minutes for its CPU image on 4
  threads), with the same sites,
  genotypes and filters as Google's image. With it installed, `--gpu` uses it
  for the WGS model; `deepvariant_native = "off"` keeps Parabricks. Other
  models keep Google's image and Parabricks.
- `pixi install -e deepvariant` installs `wgse-deepvariant`'s CUDA build on
  Linux x86-64 into an opt-in Pixi environment, where `vcf deepvariant` finds it.

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
