# Hardware and acceleration

Alignment (`align`, `realign`) is the slowest step for a whole genome. This
page says which hardware speeds it up and by how much. Each figure is labelled:

- **measured**: timed by this project;
- **published**: from the cited upstream source, on their hardware;
- **estimate**: a model or expectation, not a timing.

For memory and disk planning, see [resource estimates](resource_estimates.md).

## CPU aligners

`--aligner auto` (the default) aligns short reads with bwa-mem2 when all of
these hold, and with bwa otherwise:

- bwa-mem2 will run: upstream bwa-mem2 is installed (it is in the default
  environment on Linux, macOS and Windows), or, on Apple Silicon with native
  aligners on, `wgse-bwa-mem2` 0.5.0 or later is (upstream bwa-mem2 isn't
  needed then);
- the reference already has the bwa-mem2 index that aligner reads: the full
  index for upstream bwa-mem2 (and `wgse-bwa-mem2` before 0.5.0), without the
  `.0123` file for `wgse-bwa-mem2` 0.5.0 and later;
- the estimated alignment pipeline fits in the RAM available now.

Auto never downloads or builds the index. When the index is missing but would
fit, the CLI logs a hint; when the index is there but doesn't fit, it logs the
estimated need and uses bwa. `--aligner bwa` or `--aligner bwa-mem2` chooses
explicitly; `--aligner bwa-mem2` fails when it can't run in this computer's
memory rather than falling back.

| | bwa | bwa-mem2 |
| :--- | :--- | :--- |
| Index in memory, human genome | ~5 GB | ~17 GB |
| Building the index | far less | ~28 bytes per base, ~90 GB for GRCh38 |
| Alignment pipeline, 30x at 6 threads with `--memory 256M` (**estimate**, calibrated on a 680-million-read genome) | ~18 GiB | ~29 GiB |
| The same with default `--memory auto` on a 32 GiB computer (1 GiB per thread) | ~24 GiB | ~29 GiB (sort capped at 256 MiB) |
| Time | 1× | ~0.74× (**measured**: 192 s against 259 s, 4.2M read pairs, 12 threads) |

Few computers can build the bwa-mem2 index, so
`wgsextract ref index --aligner bwa-mem2 --ref REF` downloads the published
one (17 GB) for hs38DH and Broad's GRCh38, whose sequences are the same. For
other references it builds the index only when the computer has the memory.
bwa-mem2 is a reimplementation of bwa's algorithm, but its records are not
identical to bwa's: on 5M HG002 read pairs on hs38 the record hashes differ
(bwa family `e2febf171215d840`, bwa-mem2 family `dff39743a72242de`). Each
family is reproducible: `wgse-bwa` and `wgse-bwa-mem3 --compat=bwa-mem` write
bwa's records, `wgse-bwa-mem2` and `wgse-bwa-mem3 --compat=bwa-mem2` write
bwa-mem2's.

The ~1.3× speedup is what we measured. bwa-mem2's authors **published** up to
3.5× on one thread and 2.4× on one socket of an Intel Xeon Skylake with
AVX-512 ([Vasimuddin et al., IPDPS 2019](https://arxiv.org/abs/1907.12931)).
Bioconda's x86-64 bwa-mem2 (Linux, Intel Macs) picks its SSE4.1, SSE4.2, AVX, AVX2 or AVX-512 kernels at
run time, so newer CPUs gain more.

**ARM (Apple Silicon, Linux ARM64).** The ARM bwa-mem2 builds translate those
x86 SIMD kernels to NEON with [sse2neon](https://github.com/DLTcollab/sse2neon),
so expect a smaller gain than on AVX-512 x86 (**estimate**). On a 16-core Linux
ARM64 server, full 30x bwa-mem2 was 19.8% slower than on the x64 control
(**measured**; see [Linux ARM64](linux-arm64.md#qualification-status)).

There is no fixed memory cutoff, on a Mac or anywhere else: auto compares the
estimate for this input, reference and settings with the RAM available at the
time. As guidance only, a whole human genome needs a computer with 32 GB or
more for bwa-mem2; since the 30x estimate at 6 threads is ~29 GiB, auto picks
bwa whenever less than that is free. Smaller inputs need less.

With `wgse-bwa-mem2` 0.5.0 or later installed (and native aligners on), auto
uses its estimate, which leaves out the `.0123` file it doesn't load (about
6 GiB less for hs38; see [Apple Silicon](#apple-silicon-metal)), and the
reference needs no `.0123` file. On a 16 GB Mac a 30x genome still doesn't fit,
so auto keeps bwa there (`wgse-bwa`). `wgse-bwa-mem3` alone doesn't make auto
pick bwa-mem2: it already stands in for bwa with bwa's records. When auto
picks bwa-mem2, `wgse-bwa-mem3` stands in for it only when its own estimate
also fits the RAM available; otherwise `wgse-bwa-mem2` aligns.

Records: because bwa and bwa-mem2 write different records, a change in
auto's choice changes the BAM, not just its `@PG` header and the result
manifest. On a Mac that gains `wgse-bwa-mem2` 0.5.0 or later, or with a
reference that has bwa-mem2's index without `.0123`, auto can now pick
bwa-mem2 where it used to pick bwa, and a re-run gives a different BAM; it
logs when it does. To compare runs, pin the aligner with `--aligner bwa` (or
`--aligner bwa-mem2`).

### bwa-mem3 on Linux

[bwa-mem3](https://github.com/fulcrumgenomics/bwa-mem3) writes bwa's exact
records with `--compat=bwa-mem` and bwa-mem2's with `--compat=bwa-mem2`, from
bwa-mem2's index without its `.0123` file, several times faster. Bioconda's
build needs a newer htslib than the default environment's samtools, so it has
its own environment; install it with `pixi install --locked -e bwa-mem3` (Linux
and macOS). On Linux, when it is installed and the reference has bwa-mem2's
index, `align` and `realign` run it in place of bwa or bwa-mem2 (whichever
`--aligner` or `auto` chose), under the same memory rules as `wgse-bwa-mem3` on
a Mac ([Apple Silicon](#apple-silicon-metal)): in bwa's place its pipeline must
fit the RAM available, so on 16 GB computers bwa usually runs. Ultima reads keep
BWA. `--native-aligners off` (or `native_aligners = "off"`) turns this off. A
bwa-mem3 older than 0.16.0, or one whose `bwa-mem3 version` fails, raises the
`native_aligner_unusable` warning and bwa or bwa-mem2 runs. The result manifest
records `native_aligner` `bwa-mem3` and `bwa_mem3_mode`, as on a Mac.

**Measured** with `wgsextract align` on the first 5M HG002 read pairs (30x
NovaSeq) on hs38, 16 threads, BAM output, two rounds in reversed order, on
Azure 16-vCPU, 64 GiB VMs with Ubuntu 24.04. The records were the same with and
without bwa-mem3 (SHA-256 of `samtools view`, 10,062,598 records):

| Computer | `--aligner bwa` | with bwa-mem3 | `--aligner bwa-mem2` | with bwa-mem3 |
| :--- | ---: | ---: | ---: | ---: |
| ARM64: Cobalt 100 (Neoverse N2), D16pds_v6 | 295 s, 287 s | 52 s, 51 s (−82%) | 245 s, 243 s | 53 s, 53 s (−78%) |
| x86-64: AMD EPYC 9V74 (Zen 4), D16ads_v6 | 342 s, 334 s | 55 s, 56 s (−84%) | 181 s, 181 s | 57 s, 57 s (−69%) |

Peak memory was 14.7 GiB with bwa-mem3, against 6.6 GiB with bwa and 18.7 GiB
with bwa-mem2. The `Linux bwa-mem3 parity` workflow checks Bioconda's build
against bwa's and bwa-mem2's records on GitHub's x86-64 and ARM64 runners.

Bioconda's x86-64 bwa-mem3 0.16.0 crashes at startup (in its bundled
allocator) on glibc 2.44, as on current Arch Linux; it ran on Ubuntu 24.04's
glibc 2.39 (#727). The CLI's version check catches that: standing in, it warns
(`native_aligner_unusable`, naming the crash and the computer's glibc) and bwa
or bwa-mem2 aligns; `--aligner bwa-mem3` and `--fast` stop before aligning
with an error that says so.

### Threads and memory

- `--threads` (or `threads` in `config.toml`) defaults to `auto`: all cores, or
  the performance cores on Apple Silicon.
- `--memory` is the sort memory per thread. `auto` uses 25% of RAM (at least
  2 GB) split across the threads, with at least 1 GiB per thread; for example
  16 GiB and 12 threads gives 1 GiB each, 12 GiB in total. With bwa-mem2,
  sorting is capped at 256 MiB per thread to leave room for its index; bwa
  keeps the full `--memory`.
- Before a native BWA run, the CLI prints a memory and disk preflight. More
  threads use more memory; when it reports `LOW`, lower `--threads` or
  `--memory`, or use `--aligner bwa`. Threads are never lowered automatically.

### Around the aligner

Short reads stream from the aligner through samblaster into `samtools sort`
(or through `samtools fixmate`, `sort` and `markdup` for single-end reads or
when samblaster is missing). The last samtools stage writes the `.bai` or
`.crai` as it writes the BAM or CRAM, so there is no separate pass that reads
the whole output again to index it. The records are unchanged, and the index is
the one `samtools index` writes (#545).

The gain is modest. Measured at 4 threads on an 8-core Linux workstation
(#545):

- **Index pass, 30x (681M records, 26.5 GB BAM):** `sambamba index`, which
  Linux used before, took 97 s and 384 CPU-s, and `samtools index` 54 s. Writing
  the index during the BAM write added 62 CPU-s, so about 320 CPU-s and a
  1.5-minute pass over the whole BAM are saved.
- **Stages after the aligner, 10M reads:** samblaster used 12 CPU-s and
  `samtools sort` 67; bwa itself is far larger. Converting the
  SAM to uncompressed BAM before the sort saved nothing, and samblaster only
  reads SAM. Sorting with 2 threads instead of 4 used 12% less CPU but took
  24% longer, so the sort keeps all threads.
- **samblaster or samtools markdup:** `fixmate | sort | markdup` used 74
  CPU-s and 34 s, against 67 CPU-s and 30 s for samblaster and sort, and
  marked 5,884 duplicate records instead of 5,894. samblaster stays the
  default for paired reads.

## NVIDIA GPUs (`--gpu`, Parabricks)

`align --gpu` and `realign --gpu` run [NVIDIA Parabricks](https://docs.nvidia.com/clara/parabricks/):
`fq2bam` (BWA-MEM, sorting and duplicate marking) for short reads, and its
minimap2 for PacBio HiFi. The [README](../README.md#nvidia-gpus-parabricks)
lists exactly what each command runs.

Requirements, which the CLI checks but doesn't install:

- Linux on x86-64 (WSL2 should work but is untested; native Windows, macOS and
  Linux ARM64 are refused);
- an NVIDIA GPU with 16 GB or more and its driver;
- Docker with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/);
- the Parabricks image (`parabricks_image` in `config.toml`).

Before starting Parabricks, the CLI reads the GPU's free memory from
`nvidia-smi`. When another program holds part of the GPU, it uses the tool's
low-memory mode. When even that won't fit, or the computer has too little RAM
(fq2bam needs at least 34 GiB, and 41 GiB for a whole genome), it stops
before running, or warns when it only falls short of the whole-genome need;
`--no-gpu-memory-check` overrides this. NVIDIA recommends 100 GB of RAM for
one or two GPUs, but the 62 GB host measured below works. See [resource estimates](resource_estimates.md#nvidia-gpus-parabricks).

How much faster:

| Hardware | 30x genome, FASTQ to sorted, duplicate-marked BAM | Source |
| :--- | :--- | :--- |
| RTX 3090 (24 GB) | 40–50 minutes, against 3.5–4 hours for bwa on 12 CPU threads | **measured** |
| NVIDIA DGX system | as little as 6 minutes, against 4–9 hours on a 96-vCPU m5.24xlarge | **published**, [Parabricks fq2bam](https://docs.nvidia.com/clara/parabricks/tool-reference/tools/fq2bam) |

What `--gpu` refuses:

- **ONT**: the GPU path isn't qualified to keep the MM/ML methylation tags;
  align ONT reads without `--gpu`.
- **Ultima**: Parabricks drops the flow tags (`tp`, `t0`) and can't mark
  flow-based duplicates.
- **PacBio CLR/subreads**: there is no GPU preset.
- **`--aligner bwa-mem2`**: Parabricks runs its own BWA-MEM; use `auto` or `bwa`.

## NVIDIA GPUs without Parabricks (`wgse-bwa --cuda`)

On Linux x86-64, WGS Extract's `wgse-bwa` 0.10.0 or later (bwa 0.7.19) can run
on an NVIDIA GPU, as `wgse-bwa --metal` does on a Mac
([native/README.md](../native/README.md#linux-x86-64-and-nvidia-gpus-cuda)).
It writes bwa's exact alignments and is **experimental**.

- **When it runs:** with `native_aligners = "auto"` (the default), `align` and
  `realign` run `wgse-bwa --backend auto` in place of bwa when it's on `PATH`.
  It uses the GPU when its capabilities report `cuda_available`, and otherwise
  aligns on the CPU. If the GPU fails during a run, it finishes on the CPU with
  the same alignments.
- **Order on Linux (bwa's records):** [bwa-mem3](#bwa-mem3-on-linux)
  `--compat=bwa-mem` when it's installed, runs and fits; otherwise `wgse-bwa`
  on the GPU when it fits in RAM; otherwise `wgse-bwa` on the CPU; otherwise
  bwa. All write the same records. bwa-mem3 goes first even with a GPU: on an
  RTX 3090 with a Ryzen 5 5600X (5M HG002 pairs on hs38, 8 threads), bwa-mem3
  0.16.0 `--compat=bwa-mem` on the CPU took 97.7 s (761 s of CPU time) and
  `wgse-bwa --cuda` 131.4 s (1006 s), against upstream bwa's 472.5 s, all with
  bwa's records (**measured**, #674/#736; bwa-mem3 as `wgse-bwa-mem3`, the same
  engine built without Bioconda's malloc override, which crashes on that
  host's glibc 2.44).
- **What it needs:** only the NVIDIA driver; no Docker and no Parabricks
  licence. It checks the GPU's free memory before uploading the index (about
  4.7 GiB for hs38). It needs about 0.75 GiB more RAM than bwa, and the same
  sort-memory reduction as on a Mac applies.
- **Provenance:** the result manifest records `native_aligner_backend` as `cuda`.
- **`--gpu`:** still means Parabricks, which also sorts and marks duplicates on
  the GPU.

On Linux ARM64 (for example Graviton, Ampere or Cobalt), the linux-aarch64
`wgse-bwa` package has no GPU backend; its CPU backend runs bwa's steps with
the same exact NEON replacements as on a Mac, and `align` and `realign` run it
in place of bwa the same way (`native_aligner_backend` is `cpu`). On a 16-vCPU Cobalt 100 (Neoverse N2), wgse-bwa 0.10.0
aligned the first 5M HG002 pairs on hs38 in 224 s against Bioconda bwa's 282 s
(−20%, median of three interleaved rounds, 16 threads, `-K 10000000`), with
the same alignments (**measured**, `native/bench/bench.sh`).

## Apple Silicon (Metal)

WGS Extract publishes its own builds of four aligners for Apple Silicon Macs:
`wgse-bwa` (bwa 0.7.19), `wgse-bwa-mem2` (bwa-mem2 2.3), `wgse-bwa-mem3`
(bwa-mem3 0.16.0) and `wgse-minimap2` (minimap2 2.31). Each writes the same
alignments as its upstream release and reads the same index files; `wgse-bwa`,
`wgse-bwa-mem2` and `wgse-bwa-mem3` can run some of the work on the Metal GPU.
Install them as the [Installing](../native/README.md#installing) section of
`native/README.md` describes; WGS Extract doesn't install them.

When one is on `PATH`, `align` and `realign` run it in place of bwa, bwa-mem2
or minimap2 (`native_aligners = "auto"`, the default; `--native-aligners off`
or `native_aligners = "off"` in `config.toml` turns this off). The upstream
aligner is still needed: it builds missing indexes. Each run asks the build for
its `capabilities --json` once and logs which binary runs; a build that fails
the check, or is older than the oldest supported release, raises the
`native_aligner_unusable` warning and the upstream aligner runs instead.

- **bwa-mem3.** bwa-mem3 writes bwa's exact records with `--compat=bwa-mem`
  and bwa-mem2's with `--compat=bwa-mem2` (`@PG` aside), from bwa-mem2's index
  without its `.0123` file. On 5M read pairs on hs38 it was 1.8 times as fast
  as `wgse-bwa` and 1.5 times as fast as `wgse-bwa-mem2` on the GPU, for about
  11 GiB of memory against 6 and 11.5 GiB. So when the reference has that
  index, `wgse-bwa-mem3` runs in place of bwa or bwa-mem2 (whichever
  `--aligner` or `auto` chose) in the matching `--compat` mode, if its
  pipeline fits:
  - In bwa's place it needs more memory than bwa, so the alignment's memory
    estimate with it must fit the RAM available when the run starts (and the
    Mac's RAM less 2 GiB). On 8 to 16 GB Macs `wgse-bwa` usually runs instead.
  - In bwa-mem2's place it needs less memory than bwa-mem2, so it runs when
    its estimate fits the Mac's RAM less 2 GiB or is no more than that of the
    bwa-mem2 that would run instead (`wgse-bwa-mem2` 0.5.0 and later, which
    also leaves out the `.0123` file, may need less, and then runs).
    An explicit `--aligner bwa-mem2` still stops before aligning (and before
    `realign` extracts FASTQs) when that estimate doesn't fit the RAM
    available, as bwa-mem2's own would.

  Otherwise `wgse-bwa` or `wgse-bwa-mem2` runs as below. Ultima reads keep
  BWA. The records are the same either way; see the parity results below.
- **Metal.** `wgse-bwa`, `wgse-bwa-mem2` and `wgse-bwa-mem3` run with
  `--backend auto`, using the GPU when their capabilities report
  `metal_available`. If the GPU fails during
  a run, they finish on the CPU with the same alignments. `wgse-minimap2` runs on
  the CPU.
- **Memory.** `wgse-bwa`'s GPU backend needs about 0.75 GiB more than bwa (8.76
  against 7.96 GB peak for 5M read pairs on hs38 at 8 threads), and 0.76 GiB
  more for a denser suffix-array sample after 20M reads. When the alignment's
  memory estimate with it exceeds the Mac's RAM less 2 GiB, the sort memory per
  thread is lowered (768M, 512M, 384M, then 256M; sorting more often doesn't
  change the output). When the CPU run's own estimate is larger than that, as
  for a 30x genome on a 16 GB Mac, the GPU run only has to fit within it: the
  CPU run would go ahead regardless, about four times slower.
  When `realign` streams reads from the input into the aligner, the estimate
  must also fit the RAM available when it starts, so the GPU never pushes it
  to extracting FASTQs. If even 256M doesn't fit,
  `wgse-bwa` runs on the CPU. The batch size (`-K`) never changes.
  `wgse-bwa-mem2` is chosen only when
  bwa-mem2's own (CPU) memory estimate fits, so a GPU too small for its index
  tables, where it finishes on the CPU, still fits. Before 0.5.0 that estimate
  counts the 6.2 GB `.0123` file (hs38), which keeps it off 16 GB and smaller
  Macs; from 0.5.0 neither backend loads that file (10.9 GiB peak for 5M read
  pairs on hs38 at `-K 10000000`, against 16.3 GiB for 0.4.1), so the estimate
  and the explicit `--aligner bwa-mem2` memory check leave it out, and the
  reference needs no `.0123` file for it.
- **Provenance.** The `@PG` header names the binary that ran and its version
  (for example `VN:0.7.19-r1273-wgse-0.7.1`). The result manifest lists it under
  `tools` and records the `native_aligner`, `native_aligner_version` and
  `native_aligner_backend` (`metal`, `cuda` on Linux, or `cpu`) metrics; bwa-mem3 runs also
  record `bwa_mem3_mode` (`compat=bwa-mem`, `compat=bwa-mem2` or `fast`).
- **Dense suffix-array file (optional).** `wgse-bwa` 0.11.0 and later can
  read a side file beside the reference's BWA index that holds the
  suffix-array position of every 16th row, so it locates seeds with shorter
  walks: the same alignments, 11.5% faster on the CPU and 7.7% faster on the
  GPU for 5M read pairs on hs38. The file takes 0.80 GB on disk for hs38 and
  as much RAM while aligning. It is opt-in and never built or used
  automatically:
  - `wgsextract ref dense-sa --ref <fasta>` builds it with `wgse-bwa
    densify-sa` (it shows the disk it will take first; `--interval 8`, `4` or
    `2` makes a larger, faster file), `--list` shows the FASTA's files and the
    one `wgse-bwa` would read here, and `--delete` removes them.
  - `align` and `realign` read it with `--dense-suffix-array`, or
    `dense_suffix_array = true` in `config.toml` (`--no-dense-suffix-array`
    turns that off for a run). Otherwise wgse-bwa 0.12.1 or later gets
    `--dense-sa off`, so a file left beside the index isn't read.
    Production 0.11.0 and 0.12.0 cannot parse that switch: the CLI omits it
    when no side file exists, but refuses alignment if one is present.
    Upgrade to 0.12.1 or delete the side files with
    `wgsextract ref dense-sa --ref <fasta> --delete`;
    those older builds cannot safely honor the opt-in setting.
    The memory estimate counts the file as index memory (with the GPU it
    replaces the denser sample the GPU
    would build after 20M reads), and the manifest records
    `dense_suffix_array_interval`.
  - It is used only when `wgse-bwa` aligns. When another aligner runs, such
    as `wgse-bwa-mem3` standing in for bwa or bwa-mem2 (faster, with the same
    records), or bwa-mem2 chosen by `--aligner auto`, that aligner is kept.
    Asked to use the file, a run that can't (another aligner, no file,
    `wgse-bwa` missing or older than 0.11.0, or a file `wgse-bwa` won't read:
    it needs 16 GiB of RAM or more, a file of at most an eighth of it, and an
    intact file made from this index) raises the `dense_suffix_array_unused`
    warning and aligns without it.

`--aligner bwa-mem3` asks for bwa-mem3 itself: `wgse-bwa-mem3` on Apple
Silicon, else (or with `--native-aligners off`) `bwa-mem3` on `PATH`, for
example from Bioconda on Linux; WGS Extract installs neither, and the run
stops if neither is there or the reference lacks bwa-mem2's index. It writes
bwa's records (`--compat=bwa-mem`). `--fast` (which implies `--aligner
bwa-mem3`) runs bwa-mem3's speed preset instead, about twice as fast again,
whose alignments differ from BWA's: on 5M HG002 read pairs on hs38, 3.5% of
primary records were placed differently (92% of them reads BWA gave a MAPQ
below 20) and 2.1% more kept their place with a different MAPQ. So it is
never chosen automatically and the run raises the `bwa_mem3_fast` warning.

Parity on the M1 Pro (SHA-256 of the records, `@PG` aside): `wgsextract
align` gave the same BAM records with `wgse-bwa-mem3` standing in as with bwa
and bwa-mem2 on the fake genome, on the same genome with three ALT contigs
(ALT-aware through `<ref>.alt`) and on 5M HG002 read pairs on hs38; see
[#542](https://github.com/WGSExtract/wgsextract-cli/issues/542).

The builds are `macos-aligners-N` prerelease assets; see
[native/README.md](../native/README.md) for their measured speed.
[A 30x genome on a 16 GB Apple M1](apple-silicon-16gb.md) measures every
command, and a whole genome from `realign` to `report`, on the smallest Apple
Silicon Mac.

### bcftools with mimalloc

On macOS about a third of `bcftools mpileup`'s time goes to the system
allocator. `wgse-mimalloc` is WGS Extract's build of
[mimalloc](https://github.com/microsoft/mimalloc) 3.6.1 with malloc override on
(conda-forge's has it off), from [packaging/mimalloc](../packaging/mimalloc).
The published [wgse-mimalloc-1 package](https://github.com/WGSExtract/wgsextract-cli/releases/tag/wgse-mimalloc-1)
is pinned by URL and SHA-256 and included in the default Pixi environment on
Apple Silicon; `pixi install --locked` installs it alongside bcftools.
When it is installed in bcftools' environment, `vcf snp` and `vcf indel` start
their bcftools processes, and only those, with `DYLD_INSERT_LIBRARIES` pointing
at it (`mimalloc = "auto"`, the default; `--mimalloc off` or `mimalloc = "off"`
in `config.toml` turns this off). The CLI never installs it at runtime.

The records are the same. On 5M HG002 read pairs on hs38 with 9 threads on the
M1 Pro (medians of 3 interleaved runs), `vcf snp` took 102 s against 132 s and
`vcf indel` 155 s against 193 s, with a quarter less CPU time.
Each run first checks that bcftools loads the library (macOS drops `DYLD_*`
variables for protected binaries, and a damaged file stops it starting); if not,
or if `DYLD_INSERT_LIBRARIES` is already set, bcftools runs with the system
allocator and the log says why. The result manifest's `bcftools_allocator`
metric (`mimalloc` or `system`) records which ran. Linux isn't covered yet.

`vcf clair3` runs on Apple Silicon on the CPU only: Clair3 2.0.5 has no Metal
(MPS) path; see [docs/ont.md](ont.md#clair3-optional).

## Other GPUs

WGS Extract has no GPU alignment for AMD (ROCm) or Intel (oneAPI) GPUs: we know
of no maintained aligner for them whose output matches BWA-MEM. Integrated GPUs
give no meaningful benefit.

## When a GPU is worth it

- **One or a few genomes:** a CPU is fine. A 30x genome aligns overnight on 8–12
  threads; set up bwa-mem2 first if you have the memory.
- **Many genomes, or quick turnaround:** an NVIDIA GPU with 16 GB+ on Linux
  turns hours into tens of minutes. A rented cloud GPU works too.
- **Variant calling:** `vcf deepvariant --gpu` and `vcf gatk --gpu` run
  Parabricks' DeepVariant and HaplotypeCaller, about a minute for chr20 on the
  RTX 3090 (**measured**). CPU DeepVariant is much slower. Apple Silicon has
  [wgse-deepvariant](../native/deepvariant/README.md), a native build with
  call_variants on the GPU: chr20 in about 5 minutes on an M1 Pro, with the
  same calls as Google's image (**measured**). Linux ARM64 has a
  [native CPU build](deepvariant-arm64.md); native Windows has no DeepVariant
  ([Windows](windows.md)).
