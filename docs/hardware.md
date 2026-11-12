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
  `native_aligner_backend` (`metal` or `cpu`) metrics; bwa-mem3 runs also
  record `bwa_mem3_mode` (`compat=bwa-mem`, `compat=bwa-mem2` or `fast`).

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
