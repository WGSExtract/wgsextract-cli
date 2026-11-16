<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- Python API: quick read-only calls (`info`, `info chip`, `sample show`,
  `ref status`, `ref library-list`, `example-genome list`, `deps check`) can
  run on several threads at once, each with its own result, progress
  callback, cancel token and settings; other operations, and those asked to
  save files (`info --detailed`/`--format`, `ref library --list
  --remote-sizes`), still run one at a time. `with api.scoped_config(path):` gives one thread's calls their own
  `config.toml`. Cancelling a call now stops only its own tools. See
  [api.md](../api.md).
- `vcf acmg --research-acmg` adds automated ACMG/AMP classes from fastVEP
  (`ACMG_CLASS`, `ACMG_CRITERIA`, `ACMG_TRANSCRIPT`) to a `vep run` VCF, on the
  transcript the variant table shows. **Research use only; not a diagnosis**:
  it is off unless asked for, and every output carries the disclaimer. It
  refuses databases that lack any of its eight sources (ClinVar and its
  protein index, gnomAD v4.1 and its constraint, ClinGen, REVEL, SpliceAI,
  phyloP), which you build with `fastvep sa-build`. GRCh38; Linux and macOS,
  not Windows. See [acmg.md](../acmg.md).
- On a `vcf acmg` VCF, `vcf table` fills the columns `acmg_class`,
  `acmg_criteria` and `acmg_transcript` (empty otherwise) and records the
  research-only disclaimer in its metadata and manifest; `report` adds an
  **Automated ACMG classes (research only)** section, headed by the
  disclaimer, with the carried alleles' classes and the P/LP calls in the
  gene list's genes; and the desktop app runs `vcf acmg` once you tick that
  you understand it is research use only.
  phyloP). GRCh38; Linux and macOS, not Windows. See [acmg.md](../acmg.md).
- `ref acmg-data` downloads those eight sources and the Ensembl GFF3 from
  their publishers, checks them against the checksums they publish, shows
  their terms (REVEL and SpliceAI are non-commercial) and builds the fastVEP
  databases into the library, where `vcf acmg` now finds them. gnomAD's 877 GB
  are streamed; `--chromosomes` builds part of the genome, `--dry-run` shows
  the sizes, and reruns resume. See [acmg.md](../acmg.md).
- `vcf snp` and `vcf indel` can also write reference evidence:
  `--reference-evidence gvcf` (or `reference_evidence = "gvcf"` in
  `config.toml`) adds `bcftools.g.vcf.gz` (+ `.tbi`), a gVCF of every assessed
  base, beside the unchanged variant VCF. A shared classifier tells confident
  reference from low-quality reference, coverage gaps, filtered and no-call
  bases, and ploidy-0 bases such as a woman's chrY. Off by default: it adds a
  second bcftools pass. See
  [reference-evidence.md](../reference-evidence.md).

- `vcf deepvariant --reference-evidence gvcf` (or the same `config.toml`
  setting) also writes `deepvariant.g.vcf.gz` (+ `.tbi`) from the same run, on
  the CPU (Google's image or `wgse-deepvariant`) and with Parabricks
  (`--gpu`); the variant VCF is unchanged. On HG002 chr20 it added about 15-20%
  to the run and 21 MB. `wgse-deepvariant` keeps the whole call set in memory
  to write it, so its preflight allows about 20 GiB for a whole genome (5
  without).

- `vcf gatk --reference-evidence gvcf` writes `gatk.g.vcf.gz` (+ `.tbi`):
  HaplotypeCaller's gVCF, banded by GQ at 10, 20, 30 and 60 (about 1.4 GB a
  genome, against 6.5 GB with GATK's default bands), and the VCF genotyped
  from it by GenotypeGVCFs. Not with `--gpu` or `--scatter`.
  `vcf clair3 --reference-evidence gvcf` writes `clair3.g.vcf.gz` from the
  same run, on the CPU or the GPU; it is about 2.5-6x slower, which the run
  warns about. A man's haploid chrX and chrY are called apart from the rest,
  and their gVCF parts are cut at the partition edges and gathered in order.

- `wgse-deepvariant` 0.3.1 writes a gVCF PL value for every genotype, those
  with `<*>` included, as Google's image does; 0.3.0 left them out of variant
  records, which tools reading PL by genotype index would misread (#999).

- Low-memory Macs retain Metal BWA rather than the slower CPU fallback when
  compressed staging fits the existing physical-RAM gate. Alignment no longer
  overlaps duplicate marking/sorting; bounded compressed BAM checkpoints avoid
  large temporary consolidations. Whole HG00235x on the 16GB M1 matched all
  ordered SAM records in 6.44h including verification, with 0.073GiB swap-out
  and 42.31GiB minimum free disk. The explicit GPU speed preference still
  permits over-budget concurrent execution. See
  [the 16GB measurements](../apple-silicon-16gb.md#defaults-for-16-gb).

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
- `vcf deepvariant` also runs `wgse-deepvariant` on Linux x86-64 (0.3.1, the
  new `linux-deepvariant` conda packages): DeepVariant 1.10.0's WGS and
  ONT_R104 models without Docker, on the CPU, or with `--gpu` on an NVIDIA
  GPU through CUDA instead of Parabricks (call_variants for HG002 chr20 in 20 s on an RTX 3090,
  against 79 s for Google's GPU image and 15 minutes for its CPU image on 4
  threads), with the same sites,
  genotypes and filters as Google's image; on HG002's ONT chr20, 2 of 387,476
  sites differ, outside GIAB's confident regions. With it installed, `--gpu`
  uses it for both models; `deepvariant_native = "off"` keeps Parabricks.
  Other models keep Google's image and Parabricks. Its weights
  (`wgse-deepvariant-weights-1.10.0`) are a separate download.
- `pixi install -e deepvariant` installs `wgse-deepvariant` 0.3.1's CUDA build
  on Linux x86-64 into an opt-in Pixi environment, where `vcf deepvariant`
  finds it.

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

- config.toml is checked against one schema of every setting's type, range
  and choices when it loads: an out-of-range port or job count, or a value
  that isn't one of a setting's choices (such as `server_mcp_transport` or
  `microarray_quirks`), stops the run with an error naming the setting,
  where before the command that used it failed later or ignored it.
  [configuration.md](../configuration.md) lists every setting and is
  generated from that schema. Developer and test settings
  (`real_genome_test_path`, `real_genome_tests_enabled`, `pet_*`, and
  `dev_download_cache[_ttl_seconds]`, now `download_cache[_ttl_seconds]`)
  moved into a `[dev]` table; a config.toml that still has one at the top
  level fails to load and says where it goes.

## Fixes

- `align` and `realign` no longer admit an over-budget `wgse-bwa` GPU plan
  just because the CPU estimate is also oversized. Native BWA GPU selection
  now respects physical RAM less OS headroom without penalizing large-RAM
  hosts for momentarily low available memory; when
  it falls back to CPU, an also-over-budget sort is capped at 256M per thread.
  Smaller or already fitting CPU requests are retained. Workers and fixed
  K100M are unchanged for alignment identity. CPU fallback can be much slower
  than older GPU measurements; this addresses the excessive swap traffic on
  16 GB Macs tracked in #747 (low-memory realignment). The same whole HG002
  35x run retained every ordered record and reduced swap-out from 1,116.92
  to 47.87 GiB (95.7%), but alignment took 13.63 h rather than 5.40 h:
  reduced SSD swap traffic, not swap-free execution or a speed improvement.
  `--prefer-native-gpu` (or config `prefer_native_gpu = true`) opts into GPU
  speed only after normal GPU sort step-down fails: explicit memory is retained,
  while default/config memory is capped at 256M, subject to general sort limits.
  `--no-prefer-native-gpu` overrides config. Fallback messages name
  the roughly 2.5× measured slowdown and this override. Manifest/resume
  diagnostics retain the backend reason, estimate/budget bytes and requested/
  actual sort memory.
  The whole Metal/sort256 comparison kept the gold records at 5.08 h, but
  swap-out was still 378.39 GiB (versus CPU's 47.87 GiB), failing the 200 GiB
  gate. CPU therefore remains the low-memory default when no GPU plan fits.
  An ineffective speed preference no longer changes the reason or reruns a
  completed GPU alignment.

- Runs stopped with Ctrl-C or SIGTERM report `cancelled`, rather than `failed`,
  when a cleanup failure replaces the stop-signal handler's exit (#717).
