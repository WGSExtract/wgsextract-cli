# CLI resource preflight and reusable estimates

For measured Apple Silicon GATK PairHMM slice timings, see
[NEON prototype evidence](hardware.md#experimental-apple-silicon-neon-pairhmm).
These are not full-chromosome or whole-genome measurements and do not
change the caller's resource-budget formulas.
The incomplete full-chr20 Java trial reached only chr20:29,840,219 after
283.6 traversal minutes before cancellation; it supplies neither a completed
runtime nor peak-RSS estimate. Do not size a full-chromosome run by multiplying
the 10–20 Mb slice time: slow pericentromeric regions are absent from that slice.
The replacement 12.551 Mb panel measured **6,632.06 s Java → 725.96 s
NEON double**, with maximum process RSS **1,883.9 → 1,509.2 MiB**.
Its **9.14× observed speedup is not scientifically qualified**: nine reference
blocks and eight final-VCF annotation rows fail the strict parity gate.
PairHMM still consumes 40.3% and 64.0% of NEON CLI wall in its two difficult
windows. See the linked per-window evidence and limitations; neither these
maxima nor the aggregate speedup size an unpartitioned full genome.

## Pipeline benchmarks

`wgsextract benchmark --help` lists datasets and profiles. Start with
generated chrM and the lighter core operation suite:

```bash
pixi run wgsextract benchmark --profile smoke --suite core --outdir out/benchmark-smoke
```

The default is synthetic; `--dataset real` is the release-backed mini-genome,
while `real-1x`/`real-30x` use public WGS data. HTTP downloads resume
`.partial` files and publish only after completion and available checksum
verification. `example-genome download` shares this downloader; FTP/Aspera
keep their specialized transports. See [download safety](reference-genomes.md#download-safety).
Keep larger runs last and budget CPU, RAM and disk first.

Runtime/workspace depend on coverage, input, hardware and toolchain.
Help and realignment stages promise no fixed times; thread-based estimates
are advisory. Choose a reproducible dataset/profile before comparing hosts.

## Imputation

`vcf impute` prints estimated memory and runtime before starting. HG002 chr20
measurements at four threads: GLIMPSE2 phase/ligate 431–464 s and 2.2–3.1 GB
peak RSS; Beagle 60 s and 3.4 GB, with 8 GiB reserved. GLIMPSE2's threads
don't speed up one sample, so `vcf impute` runs `--threads` GLIMPSE2 chunks at
once, one thread each, within 80% of the available memory (or `--memory` per
chunk), estimating each chunk's peak from its binary reference; the preflight
reports the peak of the largest chunks that run together. The extrapolated
whole-autosome GLIMPSE2 time is about 5.6 h one chunk at a time and about 1 h
with eight at once, excluding one-time panel preparation. These are not
measured whole-genome runtimes. See [imputation](imputation.md#resources) for
the chr20 timings, accuracy and limits.

## Lossless output profile measurements

The opt-in [alignment output profiles](ont.md#lossless-output-profiles)
were measured on retained ONT slices from
[#431 (ONT realignment disk growth)](https://github.com/WGSExtract/wgsextract-cli/issues/431)
and a public short-read full genome for
[#691 (slimmer alignment output)](https://github.com/WGSExtract/wgsextract-cli/issues/691).
The unchanged installed encoder default was CRAM 3.1 in these runs.
These are **single shared-host observations**, not established speed ratios
or changes to the automatic resource estimates.

### Public HG002 30× full genome

The input was Google's public HG002 NovaSeq PCR-free 30× BAM: 43,667,905,004
bytes, 723,327,664 records, against full hs38DH (3,366 reference contigs).
On lin-mid (Linux x86-64, hostname lin-omarchy), samtools/htslib 1.24 encoded
the same coordinate-sorted input with four additional threads and the
product's profile options. **Write time includes encoding and indexing
only**, not mapping or sorting. `vcf snp --threads 4 --parallel --ploidy 2`
then read each CRAM unchanged, without compatibility conversion; its time
includes calling and output, not just decoding.

| Profile | CRAM bytes | Change vs default | Write s | Read + SNP calling s | Encoder peak RSS, GB |
| --- | ---: | ---: | ---: | ---: | ---: |
| default (3.1) | 15,211,213,916 | — | 568.07 | 2,218.83 | 1.38 |
| archive (3.1/archive) | 13,785,972,677 | −9.37% | 1,762.11 | 3,627.04 | 5.19 |
| calling (3.0) | 17,034,596,626 | +11.99% | 749.56 | 3,019.26 | 1.28 |

Archive saved 1.43 GB, but took 3.10× as long to write and used 3.76× the
encoder RSS in this observation; SNP calling took 1.63× as long. The calling
profile prioritizes older-reader compatibility, not smaller or faster output.
RSS here is the encoder process's peak, **not total pipeline memory**.
Profiles ran sequentially, default/archive/calling, on SSD with warm caches
and other shared-host activity; do not compare these timings as equivalent
end-to-end runs with the earlier M1 Pro slice observation.

All three produced **4,504,821 exactly identical non-header SNP VCF records**,
including genotypes and annotations, SHA-256:
`87118740ebf81fadaec346e73d4c6cf9468976a947bb00d4160b47c441d86270`.
Decoded-record checksums also matched the source over all 723,327,664
records, including unmapped, secondary and supplementary records, checking
names, flags, sequence, qualities, coordinates, CIGAR and mates.
New CRAMs were deleted only after recording verified results, keeping one
profile's output on disk at a time; the source BAM was already on SSD.

### Retained ONT slices

The original 104 GB input is unavailable, but its retained original-input
slice and a chr20:10–11 Mb slice of the realigned output remain. The latter
contains 3,493 records with the full supplementary sequences and modification
tags. Both used full hs38, the same samtools build and four additional encoder
threads. Here write time includes **sort/encode/index** (`sort -m 256M`),
not mapping; `vcf snp` used four threads, serial traversal and diploid ploidy.

| ONT slice | Profile | CRAM bytes | Write s | Read + SNP calling s | SNP records |
| --- | --- | ---: | ---: | ---: | ---: |
| Original input | default | 70,333,743 | 1.16 | 7.17 | 3,105 |
| Original input | archive | 67,826,668 | 19.59 | 11.82 | 3,105 |
| Original input | calling | 70,187,889 | 1.04 | 7.66 | 3,105 |
| Realigned chr20 | default | 56,213,264 | 1.31 | 4.18 | 1,742 |
| Realigned chr20 | archive | 52,545,253 | 24.75 | 7.16 | 1,742 |
| Realigned chr20 | calling | 56,105,626 | 1.08 | 4.32 | 1,742 |

Archive saved 3.56% and 6.53%, respectively, versus the newly encoded default.
These slices do **not** reproduce or establish the retrospective's earlier
27% saving, nor measure a full ONT genome. Within each slice, every non-header
SNP record hashed identically across profiles. All realigned-slice records
also matched source checksums, including MM/ML/MN methylation tags.
This tests direct reader compatibility, **not ONT calling accuracy**.

Evidence from 2026-11-15 is retained on lin-mid under `out/691/`:
`full/`, `ont/`, `original-ont/` contain commands, timing/RSS JSON and logs,
result tables and identical-call hashes; `ont-checksums.tsv` records ONT
sequence/tag preservation. The full run used coordinator lease L6448
(6 cores, 14 GiB RAM, 35 GiB additional SSD reserved) and completed with exit 0.

## Whole-genome host requirement

Before a run starts, `realign` (chunked or not), `vcf deepvariant` and `vcf
clair3` print what it needs of this computer in one line, from the estimates
below and the measured whole-genome runs:

```text
Host requirement: this realign run needs ~221 GiB disk on /data/hg002, ~26 GiB RAM, ~21 h on 7 threads (time approximate).
```

That is the HG002 ONT CRAM of
[#431](https://github.com/WGSExtract/wgsextract-cli/issues/431) (103.7 GB,
streamed from its URL, 26 chunks), which wrote 185.5 GB (172.8 GiB) of CRAM
in 21.4 h on 7 threads of a 16 GB M1. The line names every filesystem the run
writes to (the output directory, and a `temp_directory` on another disk) and
sums what shares one.

- **Disk** is what the run's own preflight checks:
  - realign: extraction, sort spills and output ([long
    reads](#long-read-disk)), and BWA's missing reference indexes;
  - DeepVariant: its work directory and output, for a `--region` scaled to
    the share of the input's data it selects (its CRAI slices, or all the BAM
    index's reads on each contig it touches; the whole input when the index
    can't say);
  - [Clair3's temporary space](#clair3s-temporary-space);
  - for both callers, the CRAM 3.0 copy an older build needs of CRAM 3.1
    input, sized as staging sizes it.
- **RAM** is the estimate at the run's settings, sort memory lowered as the
  run lowers it: the aligner's ([minimap2](#minimap2-long-reads) or BWA's),
  DeepVariant's largest stage, and 16 GiB for Clair3 (15.2 GiB measured with
  `--gpu` on 6 threads).
- **Time** is approximate. It scales a measured or projected whole-genome
  time by the work (read bases, CRAM bytes or reference bases called) and
  divides by the threads, which no step does perfectly, and CPUs differ. It
  says whether a run takes minutes, hours or days:

| Step | Thread-hours for a whole genome | From |
| --- | ---: | --- |
| Short-read realign, bwa | 0.70 per Gb of reads, plus 15% | 5M HG002 pairs: 472.5 s on 8 threads, Ryzen 5 5600X (#674) |
| Short-read realign, `wgse-bwa` on a GPU | 0.19 per Gb, plus 15% | the same: 131.4 s |
| Short-read realign, bwa-mem3 (bwa-mem2 assumed the same) | 0.15 per Gb, plus 15% | the same: 97.7 s |
| Long-read realign, minimap2 | 1.4 per GB of CRAM | #431: 21.4 h on 7 threads for 103.7 GB |
| Short-read realign, minimap2 `--preset sr`; Parabricks | not estimated | |
| DeepVariant, Google's CPU image | 60 | 30x: 15 h on 4 threads (projected from chr1 and chr20) |
| DeepVariant ONT model | 113 | #431: 11.3 h on 10 threads, 51x |
| wgse-deepvariant, CPU / `--gpu` | 28 / 12 | 30x: 7 h / 3 h on 4 threads (projected) |
| Clair3, CPU / CUDA `--gpu` | 33 / 15 | chr20 in 10.3 min on 4 CPUs (projected) / #431: 2.5 h on 6 threads |

The extra 15% covers FASTQ extraction and sorting: the 35x realign on a 16 GB
M1 spent 23 of its 362 minutes extracting. That run (830 million reads, 125
Gb) took 6 h; the table predicts about 3.5 h on 8 threads for `wgse-bwa` on
its GPU, which was timed on an RTX 3090. A 30x short-read genome is about 100
Gb of reads: about 20 h for bwa on 4 threads. The callers count the bases
they call on contigs the index places reads on.

**Refusal.** The run stops before it writes anything when this computer
can't meet the requirement:

- a filesystem has less free space than the run needs on it, or
- this computer has less RAM in total (or its cgroup or Docker VM allows
  less) than the run can't do without: the aligner's index and working
  memory on one thread (for minimap2's long reads, the larger of its index
  build and its mapping: 11.9 GiB for #431's CRAM on hs38, whose index build
  measured 11.17 GiB) or DeepVariant's largest stage on one thread at
  `--batch-size 256`. Sort buffers, samblaster's table and read-scaled
  transients can swap, slowly. The floor needs the reference's size (its
  `.fai`, BWA's `.ann`, or for minimap2 an uncompressed FASTA's size);
  without it there is none.

Less RAM than the estimate at the run's settings is a structured
`host_requirement_memory` warning, not a refusal: a 16 GB Mac realigned
#431's whole genome on 7 threads by compressing memory and swapping. Lower
`--threads`, `--memory` or DeepVariant's `--batch-size` to fit. Clair3's RAM,
measured once, is only ever warned about. Free memory is the existing `LOW`
rows' business.

The refusal names the numbers and exits 1, with a `host_requirement` warning in
the result. `--no-preflight` runs anyway, the requirement turned into that
warning. With `--json`, or in the result manifest, `details.host_requirement`
has the same data: `line`, `disk` (`paths`, `needed_bytes`, `free_bytes` per
filesystem), `memory_bytes`, `memory_floor_bytes`, `memory_capacity_bytes`,
`threads`, `approximate_hours`, `shortfalls` and `enforced`. A DeepVariant
rerun that keeps finished stages needs less than a fresh one, so it only warns.
`--dry-run` and Parabricks
(`--gpu` for short reads, which has [its own check](#nvidia-gpus-parabricks))
print no requirement.

## Other pipelines

Native `align` (BWA/BWA-MEM2 and minimap2), `realign`, `bam sort`, `bam to-bam`,
`bam to-cram`, `vcf snp`/`indel`/`gatk`/`deepvariant` (CPU, every platform) and
`vcf clair3` report estimates before starting their heavy pipelines. For example:

```text
Preflight align: estimated peak / available (GiB)
Preflight memory OK      18.13 / 24.00 GiB
Preflight disk   LOW     93.86 / 80.00 GiB at /data/results
```

Each filesystem gets one disk row. Temporary files, output, output indexes,
extracted FASTQs when needed, and missing reference indexes are **added together
when they share a volume**, not separately compared with the same free space.
GATK's system temporary directory can be on a different volume. A nonexistent
output directory is checked against its nearest existing ancestor, without
creating it. Available RAM uses the existing native psutil API; disk space uses
Python's native `disk_usage`, including Windows drives.

`LOW` warns. Some commands also refuse: explicitly selected
BWA-MEM2 refuses inadequate RAM, BWA warns, auto can fall back to BWA,
whole-file sorting retains its disk guard, and `realign`, `vcf deepvariant`
and `vcf clair3` refuse what their [host requirement](#whole-genome-host-requirement)
can't get. `--no-preflight` bypasses those, not BWA-MEM2's memory guard;
informational estimates are still printed. Realignment reports before FASTQ
extraction and alignment reports again when the final aligner is selected.

Native `wgse-bwa` GPU selection must satisfy **physical RAM less
2 GiB of OS headroom**. Low momentary available RAM alone warns rather than
moving large-RAM hosts to CPU; streaming realignment keeps its existing
available-RAM guard. Sort memory is lowered through the existing candidates down to
256M per thread. An over-budget CPU estimate is not a larger GPU allowance:
if no GPU plan fits, the CPU backend runs. When the CPU pipeline with the
requested sort memory is also over budget, sort memory is capped at 256M per
thread without increasing already smaller requests. Workers and fixed
`-K 100000000` are unchanged,
preserving alignment identity. The CLI reports the resolved CPU/backend and
sort-memory estimate rather than the rejected GPU plan. A conservative CPU
estimate can still be `LOW`; CPU compression, occupied swap and cumulative
SSD swap traffic must not be conflated
([#747](https://github.com/WGSExtract/wgsextract-cli/issues/747)).
The read-only `estimate_resources` API does not change settings itself:
callers supply their resolved backend allowance and sort memory.
On macOS and Linux, `--prefer-native-gpu` / config `prefer_native_gpu = true`
can replace a RAM-budget CPU fallback after normal GPU sort step-down fails,
at the risk of heavy swap. Explicit `--memory` is retained on an override;
default/config memory is capped at 256M, subject to general sort limits.
[Windows CUDA](windows.md#wgse-bwa-and-optional-nvidia-cuda) is automatic when
RAM and commit both fit, and never bypasses either check.
`--no-prefer-native-gpu` forces CPU on Windows. The manifest's
`native_aligner_backend_reason`, `native_aligner_memory_estimate_bytes`,
`native_aligner_memory_budget_bytes`, `native_aligner_sort_memory_requested`
and `native_aligner_sort_memory_actual` explain fallback or an override and survive
resume. The fallback estimate is the smallest considered GPU plan, not the
selected CPU pipeline's estimate.
For paired Metal BWA, automatic selection also tries compressed staging before
CPU fallback. RAM is the larger of the aligner/checkpoint-writer phase and
the duplicate-marking/sort phase, not their sum. Mapper and sort thread budgets
come from the resolved [stage plan](hardware.md#apple-silicon-stage-plan), including
explicit overrides; all sort workers' buffers count toward the estimate.
The per-thread staged sort budget can step down to 256M to fit, while the
fixed scientific batch size remains K100M.
Disk is the per-filesystem phase maximum: generated FASTQs plus raw checkpoint,
raw plus compressed marked parts, then parts plus final output. Owned generated
FASTQs retire after alignment unless kept; the raw checkpoint retires only after
all marked parts are durable. Existing resumable checkpoints and owned-read
retirement are credited, never user-provided read files.
Recovery checks only the selected aligner's index files. Large sidecars use
sampled content and file metadata rather than a full read. If streamed
extraction left out single-end reads, recovery reports the saved count even
after extraction scratch files are gone.
The HG00235x calibration budgets108/115 bytes per short input read for raw/marked
BAM checkpoints and2 GiB rolling scratch; longer reads scale those allowances. Preflight also
includes the guard's safety reserve once per guarded filesystem, the extraction
collation phase, and one bounded intermediate merge group. Merge fan-in stays
at64 regardless of the operating system's file-descriptor limit.

Reasons are `memory_budget_cpu`, `memory_budget_metal_staged`,
`memory_budget_metal_low_sort` (or CUDA),
and `prefer_gpu`; preference reasons/journal changes occur only when it
replaces the RAM-safe plan. `alignment_mode="staged_metal"` and the checkpoint,
chunk-record, compression-level and minimum-free-byte metrics identify staging.
On the measured 16 GiB M1 whole HG002 35x run, automatic CPU4/sort256
reported 19.22 GiB / 10.78 GiB available and completed with identical ordered
records. Swap-out fell from 1,116.92 to 47.87 GiB, **not zero**, while alignment
wall time rose from 5.40 to 13.63 hours. This is a conservative safety/speed
trade-off, not evidence that the estimate should shrink to the 7.60 GiB RSS.
See the [whole validation and options](apple-silicon-16gb.md#whole-genome-validation-of-the-ram-adaptive-fallback).
The approved whole Metal/sort256 comparison took **5.08 h** with identical
records, but **378.39 GiB swap-out**, versus CPU's 47.87 GiB. It failed the
under-200-GiB gate, so it does not justify reducing the working-set estimate
or making an over-budget GPU plan the default. Its native footprint/wired
peaks were 10.409/5.551 GiB despite only 8.633 GiB largest-child RSS.

The memory estimate is the selected aligner's. On Linux, bwa-mem3 standing in
for bwa (`--compat=bwa-mem`, chosen first since #736) needs about twice bwa's
RAM (5M pairs on hs38: 14.7 GiB peak at 16 threads against bwa's 6.6 GiB;
`wgse-bwa --cuda` peaked at 6.3 GiB at 8 threads), so it is taken only when its
pipeline fits the RAM available; otherwise `wgse-bwa` (on its GPU when that
fits) or bwa runs, and the preflight reports that aligner's estimate
([hardware guide](hardware.md#bwa-mem3-on-linux)).

Eligible paired BWA and minimap2 `sr` realignment without `--keep-fastq` streams newly
extracted reads (forced refresh of cached FASTQs at the exact output paths
retains file handling): `samtools view | collate | fastq` writes interleaved
pairs to the aligner's stdin through ordinary process pipes. There is no
`extracted_fastqs` demand. For minimap2, streaming requires an explicit
uncompressed FASTA small enough to fit in a single index part: minimap2
would otherwise need to reread the inputs for each part. GPU short-read
realignment still uses FASTQ files.

Streaming uses file extraction's collate bucket count (20× BAM / 70× CRAM input
size against sort-stage threads × per-thread memory, 64–200 buckets).
Codec `-@` workers are independent: using efficiency cores for compression
does not enlarge collate's bucket budget or its concurrent-RAM estimate. Collate's output
order depends on that count, so the aligner receives the same pairs in the same
order either way and the realigned records are identical. A 30x genome streams
whenever one bucket fits beside alignment in RAM.

Collate holds one bucket in memory while the aligner maps. Its estimate is the largest
bucket's records (the indexed input count over the buckets, plus six standard
deviations) times the power-of-two buffer of the largest BAM record in the
read-length sample, plus 128 bytes of per-record bookkeeping and 64 MiB of
headroom. Without an indexed count it falls back to 30× BAM / 100× CRAM of the
input size per bucket. This amount is added to mapping's RAM preflight; if it
does not fit, realignment extracts files without changing the selected aligner.
It is an estimate, not an enforced RSS limit:

| Input | Records | Buckets | Measured collate RSS per held record | Estimate per record |
| --- | ---: | ---: | ---: | ---: |
| HG00096 chr20 BAM (1000 Genomes 30x, 852 MB) | 17,275,064 | 16 / 64 / 133 / 200 | 677 / 690 / 709 / 725 bytes | 1,152 bytes |
| HG002 chr20 BAM (GIAB NovaSeq 35x, 1.09 GB) | 19,467,155 | 16 / 64 / 133 / 200 | 684 / 716 / 758 / 795 bytes | 1,152 bytes |

Measured per-record figures are samtools 1.24 collate's maximum RSS over the
largest bucket's records, so fixed process memory inflates them at high bucket
counts.

Collate writes every bucket to temporary files before it emits a read, then
deletes each bucket as it drains while the sorter's spills grow. The disk
guard therefore takes the larger of collation's spill and sort spill plus
output, the same as file extraction without the FASTQs between them; the
resource report counts the sort `temporary` estimate once. With a configured
temporary directory on another volume, that volume needs the larger of the two
spills and the output volume holds the result. If streaming spills do not fit
but file extraction does, realignment uses files.
The alignment sample reads at most 4096 input SAM records / 16 MiB for the mean
paired-primary read length and the largest record of any kind; it does not
replace the indexed input count.

On 2 Mb chr20 slices (BWA, 2 threads, 512M, one core) the streamed BAM had the
same ordered records and header as file extraction except the BWA `@PG`
command line (`-p … -`); file extraction stayed byte-identical. Streaming wrote
38% less (HG00096 113 → 75 MiB, HG002 316 → 196 MiB) at the same wall time and
within 3% of the same peak RSS.

## samtools sort buffers

`samtools sort -@ N -m M` mallocs all N × M bytes when it starts, and the kernel
refuses an allocation larger than RAM plus swap however few reads arrive: the
sort fails with `couldn't allocate memory for bam_mem` and the aligner then dies
of a broken pipe (#781). An explicit `--memory` can ask for more. Automatic
memory budgets start at 25% of effective RAM (host RAM capped by the Linux
cgroup v2 limit), divided across threads and rounded down. `align`, `realign`,
`pet`, `bam sort` and `bam to-ubam` give each sort the smallest of:

- `--memory` per thread;
- what its read files can fill: 96 bytes of records per byte of input (the
  bound minimap2's estimate uses below), split across the threads, when every
  input is a local file;
- half of effective RAM, split across the threads, leaving room for the rest
  of the pipeline.

The CLI logs when that lowers `--memory`. The input bound never goes below 16M per
thread, but the RAM cap can lower that further. If the budget cannot provide
samtools' minimum of 1 MiB per thread, lower `--threads`. Records and their
order are unchanged: a sort that fits its input sorts in memory, and a sort
that spills merges its spills stably. FASTQ collation uses the total budget
to size its buckets and does not have samtools sort's per-thread minimum.
Alignment memory checks count the sort's reservation (threads × `-m`) at
least, as well as what its reads fill. `realign`'s checks before extracting
FASTQs use the requested `--memory`, so they never ask for less than its
alignment uses.

## minimap2 (long reads)

minimap2 builds its index from the reference FASTA in memory on every run;
WGSExtract keeps no `.mmi` file. Its preflight therefore counts the index for
the selected preset, plus the larger of the build's scratch and mapping's
batches and per-thread buffers, plus the sort and a fixed overhead (and
samblaster for paired `sr` reads). Only samblaster depends on the read count.
The batches, the threads' buffers and the sort's buffers fill only as reads
arrive, so each is capped at 96 bytes per byte of read file (at least one
thread's buffers stay): a read file smaller than about 30 MB fills one smaller
batch, and a few-MB FASTQ at many threads is not budgeted gigabytes of sort
memory.

Long-read presets (every preset but `sr`) are budgeted as the larger of two
phases. While the index builds, no reads reach the sort and the decoder waits
on its pipe, so that phase is the build's peak plus the fixed overhead. While
mapping, the index, the batches, 0.85 GiB of buffers per thread at a 20 kb mean
read length (scaled up for longer reads; the mean is sampled from the input),
samtools sort's 1.75 × `--memory` per thread, the fixed overhead and 1 GiB for
the CRAM/FASTQ decoder are summed, plus 10%: the whole-genome peaks below were
measured in a full cgroup, so they are lower bounds. It was calibrated with
minimap2 2.31 on hs38 (3.10 Gbases):

| Measurement | Measured peak | Estimate |
| --- | ---: | ---: |
| Index build, `map-ont` / `map-hifi` and `lr:hq` / `map-pb` / `sr` | 11.17 / 11.11 / 8.62 / 11.31 GiB | 11.40 / 11.40 / 9.75 / 12.58 GiB |
| Mapping 40k HG002 ONT reads (20 kb mean) from the `map-ont` index, 4 threads | 10.93 GiB | 12.68 GiB |
| ONT chr20 `realign`, 4 threads, `--memory 512M` (#299, whole pipeline) | 14.8 GB | 19.45 GiB (20.9 GB) |
| Whole-genome HG002 ONT `realign` (#475, 103.7 GB CRAM), 5 threads, `--memory 256M` | ≥ 15.46 GiB (killed for lack of memory) | 18.94 GiB |
| The same, 4 threads, `--memory 192M` | ≥ 15.26 GiB | 17.05 GiB |

Before #475 the whole-genome run at 5 threads was estimated at 13.85 GiB,
reported to fit in 15.46 GiB, and was killed for lack of memory after 5 hours:
minimap2 alone peaked 5.8 GiB above its index, samtools sort held 1.65–1.7 ×
`--memory` per thread, and `samtools fastq` peaked at 1.0 GiB. Short slices
peak lower, so their estimates are conservative.

When the estimate exceeds the available memory (the cgroup limit included),
`align` and `realign` log a structured `alignment_memory` warning before
minimap2 starts. It suggests the most threads, then the largest per-thread
`--memory` (down to 256M), that fit, or says that even one thread does not. The
run then continues as requested. minimap2's index takes the same memory however
few threads it has, so on hs38 no setting fits in less than about 12–13 GiB.
WGSExtract does not offer minimap2's `-I` (split index): a split index of hs38
needs `--split-prefix` and a merge pass, and maps more slowly.

The published Linux RTX 3090 ONT/HiFi speedups are slice mapping
measurements, not whole-genome pipeline estimates. The GPU accelerates gap
fills, not chaining, decoding, sorting or CRAM encoding. Until
[#1122 (Linux CUDA long-read alignment)](https://github.com/WGSExtract/wgsextract-cli/issues/1122)
records a full RTX 3090 realignment, don't use the slice speedup to predict
whole-genome runtime or reduce host resource budgets.

### Linux native ONT qualification

On 2026-11-18, 93,328 HG002 ONT reads (1.834 Gbp, approximately 1.18% of
the whole run's 7.93 million reads) were mapped to the full hs38 index on
the Ryzen 5 5600X Linux host. The input was 1.79 GB of gzipped FASTQ,
sampled from 1.5 Mb windows on each of chr1–22 and X.

| Mapper, threads | Wall | CPU time | Peak mapper RSS |
| --- | ---: | ---: | ---: |
| `wgse-minimap2` 0.4.0 CPU, 2 | 749.2 s | 1451 s | 10.43 GiB |
| Upstream minimap2 2.31, 4 | 449.9 s | 1678 s | 10.80 GiB |
| `wgse-minimap2` 0.4.0 CPU with stage timers, 4 | 440.2 s | 1661 s | 11.11 GiB |

All produced the same 6.74 GB of SAM records, excluding `@PG`, streamed to a
digest rather than retained. No mapper temporary files were needed; the shared
index occupies 7.26 GB. These single shared-host runs are not an isolated
performance or thread-scaling comparison; the native four-thread run also
includes stage-timer overhead. A separate native full-reference index build at
two threads took 51.1 s and **15.74 GiB** peak RSS on this toolchain, before
pipeline overhead. The upstream index-build estimate and the 12–13 GiB floor
above do not bound the native build's RAM needs.

Linear read-count scaling of the instrumented four-thread mapping gives about
10.4 h and 573 GB of raw SAM for the whole run, **not a measured whole-genome
time or a persisted CRAM size**. It excludes decoding the source CRAM,
preserving methylation tags, sorting and encoding, and can miss the cost of
rare repeat-rich regions. Memory does not scale linearly with read count.
The earlier chunked, URL-streamed CPU run produced 185.5 GB of CRAM. Its
preflight asked for about 211 GiB of output-volume space; that was not a
measured peak. For planning, use the current
[host requirement](#whole-genome-host-requirement) (about 221 GiB for that
chunked run) and the [long-read disk estimates](#long-read-disk), including
additional sort-spill space for unchunked runs. The
[full Windows CUDA realignment](#whole-genome-ont-requirements) below does
not qualify Linux CUDA.

### Long-read disk

Long-read `align` and `realign` scale their disk estimates with the input's
size, not a per-read size. minimap2's `-Y` keeps every supplementary alignment's
full read, quality and tags, and HG002 ONT has many: chromosomes 1, 2, 5, 12
and X carried 1.42–1.70 bases of mapped records per input base (about 1.55
genome-wide). The figures are bytes per byte of input CRAM (BAM inputs count
1.76 CRAM bytes per byte, bgzipped FASTQ 1.39 and plain FASTQ 3.3):

| Item | Estimate | Measured (#474) |
| --- | ---: | ---: |
| samtools sort spills | 3.0 | whole-genome run 2.65–2.8 when stopped at 12% of the input; full-hs38 slice 3.35 |
| Extra spill while sort merges 32 spill files into one | 33 × 0.65 × `--memory` per thread, up to the spills | samtools 1.24's merge |
| Output CRAM / BAM | 1.8 / 2.75 | slice CRAM 1.90 (1.91 bases per base); whole genome 1.79 (#431) |
| Tagged bgzipped FASTQ (`realign`) | 1.45 | 1.39 |

Chunked long-read realignment streams its sorted BAM into one reference group
at a time when pysam 0.24.1 or newer is installed. It no longer writes a
temporary BAM and CSI for each chunk. The external sort spills still need
scratch space, and the groups and assembly stay on disk so the run can resume.
Installations without this pysam version keep using the indexed BAM handoff.

`realign --tmp-dir` on another volume needs the spills there, and the output and
FASTQ in the output directory. For the 103.7 GB HG002 ONT CRAM at `--threads 5
--memory 256M` that is about 316 GiB of scratch and 174 GiB of output plus 141
GiB of FASTQ. Before #474 the guard asked for 376 GiB of scratch; the
whole-genome run stopped at 12% of its input had already spilled 33.6 GB.

## NVIDIA GPUs (Parabricks)

With `--gpu`, `align`, `realign`, `vcf deepvariant` and `vcf gatk` read each
GPU's total and free memory from `nvidia-smi` before starting Parabricks, and
report a `gpu` row beside the host `memory` row. pbrun uses every GPU, so each
GPU is checked against its own memory: low-memory mode is chosen when any GPU
is short of its default need, and a GPU that reports no memory doesn't hide
another one's shortage.

- A GPU with less than 16 GB (a T4 reports 15 GiB) is refused.
- When less memory is free than a tool's default mode needs, the tool runs
  with its low-memory option, `--low-memory` for fq2bam and minimap2 and
  `--htvc-low-memory` for HaplotypeCaller. The run logs this and records a
  `parabricks_low_memory` warning. fq2bam sizes its work from the GPU's
  *total* memory, so it needs its low-memory mode whenever another program
  holds more than about 2 GiB of the GPU.
- When even the low-memory mode won't fit, the command stops before running
  anything. DeepVariant has no low-memory mode.
- Each tool also needs host RAM, whatever the GPU mode: at least 34 GiB for
  fq2bam, and 41 GiB for a whole 30x genome, whose sort holds more (minimap2
  is assumed to match); 25 GiB for DeepVariant; 18 GiB for HaplotypeCaller.
  A computer with less RAM than the minimum is refused. When it has the
  minimum but less than the whole-genome need is available, because the
  computer is smaller or other programs hold it, the run continues with a
  `parabricks_host_memory` warning: available memory moves, and the
  kernel can often reclaim cache. The log also notes when the host has less
  RAM than NVIDIA recommends (100 GB for one or two GPUs, 49 GB a GPU beyond
  that).
- `--no-gpu-memory-check` runs anyway, with a `parabricks_memory` warning.
  When `nvidia-smi` reports no memory (`[N/A]`), Parabricks runs with its
  defaults.

Measured with Parabricks 4.8.0 on an RTX 3090 (24 GiB), 4 CPUs, on chr20 of
HG00096 (8.6 M read pairs, hs38DH). Another process held the "other" memory
with `cudaMalloc`:

| Run | GPU peak | Host RAM peak | Result |
| --- | ---: | ---: | --- |
| fq2bam | 21.5 GiB | 32.7 GiB | ok |
| fq2bam, 8 GiB held by another process (15.3 GiB free) | | | out of memory |
| fq2bam `--low-memory` | 13.5 GiB | 30.1 GiB | ok, also with 15.3 GiB free |
| fq2bam `--low-memory`, 12.3 GiB free | | | out of memory |
| DeepVariant | 12.9 GiB | 24.2 GiB | ok, also with 15.3 GiB free |
| DeepVariant, 12.3 GiB free | | | out of memory |
| HaplotypeCaller | 17.2 GiB | 16.6 GiB | ok |
| HaplotypeCaller `--htvc-low-memory` | 10.7 GiB | 16.6 GiB | ok, also with 15.3 GiB free |

The low-memory runs took as long as the defaults on this data. With the
container's RAM capped (no swap), every run was killed: fq2bam at 24 and
16 GiB, also with `--low-memory --memory-limit 8`; DeepVariant at 16 GiB;
HaplotypeCaller at 12 GiB. fq2bam's `--memory-limit 16` left its peak at
33 GiB. minimap2's 18 GB default need is NVIDIA's figure, not measured here.

The whole genome, `realign --gpu` of HG00096 (30x, 49.6 GB BAM out) on the
same host with the GPU free, ran fq2bam in its default mode with no warnings:

| Step | Time | GPU peak | Host RAM peak |
| --- | ---: | ---: | ---: |
| Extract FASTQs (CPU) | 34 min | | |
| fq2bam: align, mark duplicates, sort | 53 min | 21.6 GiB | 40.8 GiB, while sorting |
| Total | 88 min | | |

`vcf deepvariant --gpu` on Ultima reads runs Google's DeepVariant 1.10 GPU image
(`gh1100-ultima-gpu`) instead of Parabricks, and checks only that each GPU has
7.3 GiB free before call_variants. On the same RTX 3090, chr20 of GIAB HG008
Ultima reads at 30x (182,666 examples, 4 make_examples shards):

| Run | Time | Result |
| --- | ---: | --- |
| call_variants on the GPU | 3 min | ok, also with 7.3 GiB free |
| call_variants on the GPU, 6.3 GiB free | | out of memory |
| call_variants in the CPU image, 4 CPUs | 16–33 min | same calls in GIAB's easy regions; 14 of about 103,570 PASS calls differ on all of chr20 |

The example count is call_variants' final `Wrote N examples` line; its
`Predicted N examples in M batches` lines are progress reports every 50 batches.
CPU call_variants took 33 min in the #358 run and 16 min in #496; the cause of
the difference is unknown.

TensorFlow takes most of the free GPU memory whatever it needs (22.7 GiB of
the free 24 GiB GPU). make_examples and postprocess run on the CPU, as in the
CPU image.

### DeepVariant on the GPU with wgse-deepvariant

On Linux x86-64, `vcf deepvariant --gpu` uses
[wgse-deepvariant](../native/deepvariant/README.md#linux-x86-64-and-nvidia-gpus-cuda)
only for the WGS model (and from 0.3.0 ONT_R104), when it is installed,
`deepvariant_weights` is set and `deepvariant_native` isn't `off`; WES and
PacBio still run Parabricks, and
Ultima Google's GPU image, with the memory needs above. This pipeline runs only
call_variants on the GPU and
checks just before it that GPU 0 has 3 GiB free (`--no-gpu-memory-check`
skips this); make_examples and postprocess_variants stay on the CPU. Its RAM
estimate is the Mac build's (#647), which covers what Linux used.

Measured (#719) on a Ryzen 5 5600X with an RTX 3090, `--threads 4`, from the
`linux-deepvariant-1` package (0.2.0), on HG002's 35x NovaSeq BAM (GRCh38 no-alt), chr1
and chr20: 823,376 sites, 270,091 call_variants examples. Google's
`google/deepvariant:1.10.0` image ran with 4 CPUs on the same host.

| Stage | Google's CPU image | wgse-deepvariant, CPU | wgse-deepvariant, `--gpu` |
| --- | ---: | ---: | ---: |
| make_examples | 41.4 min | 18.4 min | 15.7 min |
| call_variants | 50.5 min | 22.7 min | 1.2 min |
| postprocess_variants | 0.8 min | 0.1 min | 0.2 min |
| End to end | 92.8 min | 41.3 min | 17.1 min |

- **GPU memory:** 2.3 GiB at the default batch of 256 (chr20 and chr1+chr20
  alike).
- **Host RAM (chr20):** call_variants 1.5 GiB on the GPU or the CPU;
  make_examples 0.2 GiB; postprocess_variants 0.8 GiB.
- **Same calls:** the three runs call the same 823,376 sites with the same
  genotypes and filters, and the same GIAB v4.2.1 counts in its confident
  regions (TP 386,651, FP 488, FN 2,761). QUAL differs from Google's by at most
  0.5 at 24,130 sites, and GQ by at most 2 except one RefCall site (99 against
  69, same PL); the CPU and GPU builds differ at 31 sites (QUAL ±0.1, GQ ±1).
- **A 30x genome** is about ten times chr1 and chr20: about 3 hours with
  `--gpu` (call_variants about 12 minutes), 7 hours on the CPU and 15 hours in
  Google's image, at 4 threads (**projected**). make_examples scales with
  `--threads`.

### Clair3 on the GPU

On x86-64 Linux, `vcf clair3 --gpu` runs HKU's Clair3 2.0.5 GPU image and checks that a
GPU has 7.8 GiB free: Clair3 starts a full-alignment GPU worker for each
8,000 MiB free and a pileup worker for each 5,000 MiB. On the same RTX 3090,
all of chr20 of the HG002 ONT whole-genome CRAM (42x), 4 CPUs, Clair3's own
run time:

| Run | Time | GPU peak | Result |
| --- | ---: | ---: | --- |
| CPU (`clair3` Pixi environment) | 10.3 min | | vcfeval F1 0.9785 |
| `--gpu` | 8.5 min | 6.1 GiB (4 pileup workers of 1.0–1.3 GiB) | F1 0.9785 |

The GPU halved pileup and full-alignment calling (283 s to 159 s). Clair3's
start-up checks (about 130 s on a whole-genome CRAM) and WhatsHap phasing
(about 205 s) stay on the CPU, and the GPU was busy in only about 5% of
one-second samples, so the CPUs limit the speed-up.

On a whole genome the CUDA mode needs much more disk than the CPU mode: it
writes every full-alignment tensor before predicting any (below).

On Apple Silicon, the WGSE-built `wgse-clair3` caller instead selects **MPS
inference with CPU streaming/scaling**, including gVCF. It uses CPU-path
temporary-space and time estimates, not the CUDA tensor budget or NVIDIA
memory thresholds. These time estimates are conservative placeholders, not
measured MPS whole-genome times. MPS availability and the patched package
are mandatory preflight checks; no automatic fallback or installation.
Package publication/Pixi pinning is done (`macos-clair3-1`); whole-genome
qualification is pending; see [the package release](../native/clair3/README.md#packaging-and-release).

**M1 Pro sizing, 2026-11-18/19:** 10 cores, 32 GiB, four calling threads,
Clair3 2.0.5/PyTorch 2.14.1, original-alignment public HG002 ONT reads,
`r1041_e82_400bps_sup_v400`, through the real CLI with gVCF. Only chr20 was
streamed from the public CRAM, once, into a shared 3.7 GiB BAM; the same
local BAM/reference/model served both runs. No whole-genome run was made.
The table is the awake paired repeat on 2026-11-19 with the published
`macos-clair3-1` package: `caffeinate`, CPU then MPS in one turn of the shared
bench lock (no other timed work; one-minute load 15–33 at the CPU run's start,
3–7 later, so the CPU figure may be slightly pessimistic).

| chr20, 64.44 Mb (~2.1% of 3,088 Mb) | Wall | Max process RSS / sampled tree RSS | Sampled peak temp | Published output |
| --- | ---: | ---: | ---: | ---: |
| CPU + gVCF | 20.36 min | 1.99 / 4.71 GiB | 510.92 MiB | 31.44 MiB |
| MPS + gVCF | 15.19 min | 2.09 / 5.22 GiB | 512.63 MiB | 31.44 MiB |

Both produced **142,070 identical sites/alleles/GT/FILTER**, in VCF and gVCF,
and **3,084,277 reference records identical in every column**. QUAL differed
by at most 0.03; six PL fields and two GQ fields differed by one. Peak system
swap was 1.55/1.54 GiB (left over from earlier host work); minimum available
system RAM was 12.0/10.8 GiB. Tree RSS double-counts
shared pages and is not GPU allocator/unified-memory accounting; 0.5-second
disk sampling can miss spikes.

MPS was about **1.34x** faster end to end on whole chr20. The first sizing
pair (2026-11-18) recorded 86.77 min for CPU because the host slept during the
run, and is superseded by this repeat.

For **capacity planning only**, multiplying chr20 by 47.9 gives about 24 GiB
temporary space and 1.47 GiB output. The same mechanical scaling gives
12.1 h for MPS, **not a measured or reliable whole-genome ETA**: chr20's
depth/candidate mix, including its pericentromere, is not representative.
Do not scale peak RSS linearly or assume the whole genome fits from this
slice alone. Its measured temp peak also exceeds the current CPU-path
preflight estimate (~0.18 GiB); reserve space from measurements, not that
estimate alone. Before paired whole genomes, check large-contig/gather memory
and staging disk, and use run-scoped sleep prevention and failure
notifications.

### Clair3's temporary space

Clair3 works in a `.wgse_clair3_*` folder in the output directory. In its CUDA
mode, Clair3 2.0.5 first writes a tensor of 89 x 33 x 8 bytes (23.5 kB) for
every full-alignment candidate (`tmp/full_alignment_output/*.npy`, 10,000 per file),
then predicts and deletes them; CPU and WGSE MPS pipe each tensor to the network.
Clair3 has no option to stream or batch them: `--chunk_size` changes only how
the candidates are split into files, and `--var_pct_full`/`--ref_pct_full`
change which are called, and so the calls. The candidates are a share of the
pileup calls, and shallower reads give more low-quality ones. Measured on the
HG002 ONT R10.4.1 reads (`r1041_e82_400bps_sup_v400`, 4 threads, Clair3 2.0.5's
GPU image) as the work folder's peak, sampled every 2 s:

| Reads (mean depth) | Called | Peak | Per base |
| --- | ---: | ---: | ---: |
| chr20:10-20 Mb (43x) | 10 Mb | 314 MB | 31 B |
| the same, subsampled to 33x / 22x / 15x | 10 Mb | 332 / 415 / 646 MB | 33 / 42 / 65 B |
| the same, subsampled to 11x / 6x / 4x | 10 Mb | 1.05 / 1.23 / 1.05 GB | 105 / 123 / 105 B |
| chr20:10-11 Mb (43x) | 1 Mb | 33 MB | 33 B |
| all of chr20 (43x) | 64.4 Mb | 2.92 GB | 45 B |
| the whole genome ([#431](https://github.com/WGSExtract/wgsextract-cli/issues/431) whole-genome run, 6 threads) | 3,088 Mb | 99.7 GiB | 35 B |
| all of chr20 on the CPU | 64.4 Mb | 61 MB | 0.9 B |

Before starting, `vcf clair3` samples the reads' mean depth (about 200 windows
of 1 kb) over the `--region`, taking its shallowest chromosome group (a man's
chrX), or else over the bases the reads cover. It interpolates the bytes per
base above (held flat beyond 6x and 43x), adds 45% (all of chr20 needed 1.44x
the 10 Mb slice's rate) and multiplies by the bases Clair3 calls: the `--region`, or else chr1-22,
X and Y on the contigs the index places reads on. That asks for about 131 GiB
for a 43x whole genome, 273 GiB at 15x and 449 GiB at 10x. The CPU mode is
budgeted at 2 bytes per base (about 6 GiB for a whole genome). The disk row
reports it:

```text
Preflight clair3: estimated peak / available (GiB)
Preflight disk   LOW     138.06 / 95.69 GiB at /data/hg002
Preflight: Clair3 on the GPU stores every full-alignment tensor first: 3,088 Mb at 33x.
```

Unlike the warning-only rows of other callers, a `LOW` row refuses to start
(through Clair3's [host requirement](#whole-genome-host-requirement)), since
Clair3 would otherwise fail after hours of pileup calling and phasing;
`--no-preflight` runs anyway. With too little disk, choose an `--outdir` with
room or call a few chromosomes at a time with `--region`. A CRAM 3.0 copy
that an older Clair3 build needs is checked again once it is written. On a
compressing filesystem (btrfs with zstd), the 99.7 GiB took about 60 GB of the
disk; the estimate doesn't count on that.

With `--reference-evidence gvcf` (#863) the estimate adds three copies of the
gVCF at 0.35 bytes per called base (1.6 MB for chr20:10-15 Mb; a partitioned
run's parts, their gathered copy and the published gVCF can coexist), and the
host requirement's calling time is multiplied by 3.8 on the CPU and 6 on the
GPU, the slowdowns #863's research measured.

## Whole-genome ONT requirements

Measured on the whole HG002 ONT R10.4.1 CRAM (103.7 GB, about 51x, hs38;
[#431](https://github.com/WGSExtract/wgsextract-cli/issues/431)). Details,
accuracy and caveats are in
[ONT whole-genome requirements](ont.md#whole-genome-ont-requirements).

| Step | Host, threads | Wall time | Peak RAM | Disk |
| --- | --- | ---: | ---: | --- |
| `realign`, chunked, streamed from the URL, `--memory 512M` | Apple M1 16 GB, 7 | 21.4 h | 10.5 GB (minimap2) | 185.5 GB output (1.79x input); preflight asked for about 211 GiB |
| `vcf deepvariant --ont`, Google's CPU image | Ryzen 5 5600X, 10 | 11.3 h | not recorded | from the 81.4 GB primary-only CRAM 3.0 copy: work directory not recorded, 150 GB was enough; CRAM 3.1 input also needs about 1.95x its size free for a staged CRAM 3.0 copy |
| `vcf clair3 --gpu` | Ryzen 5 5600X + RTX 3090, 6 | 2.5 h | 15.2 GiB, GPU 6.1 GiB | about 100 GiB temporary (full-alignment tensors, about 1 GiB per 1% of the genome) |
| `rtg vcfeval` of one whole-genome VCF | 4 | 1 min | 7.2 GB | |

On native Windows, [one whole-genome run per backend](https://github.com/WGSExtract/wgsextract-cli/issues/1121#issuecomment-7160085047)
used a Ryzen 9 9900X3D, RTX 5080, local NTFS storage, eight threads, and
`--chunk-size 512M --memory 256M --format CRAM --no-resume`. Wall time includes
building the hs38 index and writing the CRAM and index, but excludes download
and post-run validation.

| Backend | Wall time | Peak process-tree working set | Peak device VRAM above idle | Peak run-directory disk | Final CRAM |
| --- | ---: | ---: | ---: | ---: | ---: |
| Upstream minimap2 2.31-r1302 | 17.66 h | 13.05 GiB | 949 MiB (incidental device usage) | 210.7 GiB | 184.21 GB |
| CUDA `wgse-minimap2` 0.4.1 | 15.25 h | 15.61 GiB | 1,882 MiB | 211.6 GiB | 185.48 GB |

CUDA cut wall time by 13.6%. Both runs took longer than projected from the
[1.1% Windows slice](windows.md#wgse-minimap2-for-long-reads): 14.3 h CPU
and 12.9 h CUDA. The slice also projected less output (about 170 GB).
Working set and VRAM depend on index and chunk sizes and concurrent
processes, not total read count. The working set is not a Windows commit
budget. Budget at least 230 GiB per run for output and staging, plus
about 97 GiB for a local copy of this input if needed.

Both callers read a primary-only CRAM 3.0 copy (81.4 GB) of the realigned
output from the Apple M1 run in #431. `vcf clair3` checks
[its temporary space](#clair3s-temporary-space)
before it starts. The realign preflight first budgeted the output at 1.7x
the input CRAM; this genome needed 1.79x, so it now budgets 1.8x ([long-read
disk](#long-read-disk)). Each of these steps prints its [host
requirement](#whole-genome-host-requirement) first: for this CRAM on 7 threads,
realign asks for about 221 GiB, 26 GiB of RAM (refusing below 11.9 GiB) and
21 h.

## Methylation regions

The whole-genome site-table benchmark used **26,782,992** strand-combined
5mC rows from the [public ONT GIAB HG002 PAW70337 SUP HP1 bedMethyl][hg002-site-table]
(GRCh38, ONT GIAB 2025.01). Both formats contained the same rows in the same
order: 365 MiB of BGZF or 293 MiB of Parquet. The file was already split by
ONT; these numbers measure region summaries from a precomputed site table,
not haplotype assignment or pileup from reads.

Each row below is one local `methylation regions` run on an Apple M1 Pro
(32 GiB, samtools 1.24, PyArrow 26.0.1 with two CPU threads). Wall time
includes looking up reference CpGs and writing the TSV, but excludes download
and input conversion. Peak disk is the sampled run-directory size, including
temporary files and results, but not the existing site table or FASTA.

| Preset | Input | Wall time | Peak RSS | Peak run disk | Final TSV |
| --- | --- | ---: | ---: | ---: | ---: |
| RefSeq promoters | BGZF | 55.7 s | 198 MiB | 98 MiB | 6.9 MiB |
| RefSeq promoters | Parquet | 42.6 s | 511 MiB | 103 MiB | 6.9 MiB |
| UCSC masked CpG islands | BGZF | 52.2 s | 150 MiB | 23 MiB | 3.7 MiB |
| UCSC masked CpG islands | Parquet | 41.7 s | 483 MiB | 23 MiB | 3.7 MiB |

The TSVs matched byte-for-byte across input formats (59,045 promoter rows
and 27,949 island rows). Parquet cut elapsed time by 20–24% but raised peak
RSS. If converting an existing BGZF file, weigh that separate cost against
the scan time saved on repeated summaries.

### Haplotype split from reads (chromosome 21)

On the same Mac, a 1.55 GiB chromosome 21 slice of the
[matching ONT haplotagged CRAM][hg002-hp-alignment] was benchmarked with
modkit 0.6.4 and a plain chromosome 21 FASTA. The paired modkit runs used
identical target BEDs, strand-combined CpG 5mC, 100 kb intervals, and one
thread each for compute, I/O and compression. Only `--phased` changed.

| Preset | Pooled → phased wall time | Pooled → phased peak RSS | Pooled → all three BGZF files |
| --- | ---: | ---: | ---: |
| Promoters | 78.5 → 103.8 s | 545 → 744 MiB | 0.75 → 1.94 MiB |
| CpG islands | 71.2 → 83.0 s | 596 → 724 MiB | 0.61 → 1.59 MiB |

The `combined` output matched the pooled bedMethyl byte-for-byte in both
runs. End-to-end `methylation regions --partition-hp --threads 1`, including
the phase-tag scan and region summary, took **161 s / 760 MiB peak RSS** for
promoters and **105 s / 693 MiB** for islands. Sampled peak run disk was
24.1 and 11.4 MiB, including the final TSVs (23.6 and 11.4 MiB).
The TSVs still list all hg38 preset regions, including those with absent
contigs. The CLI removes intermediate partitions; a compressed reference
may also need a temporary uncompressed copy. Only chromosome 21 reads
were processed, so these are not whole-genome HP estimates.

[hg002-site-table]: https://ont-open-data.s3.amazonaws.com/giab_2025.01/analysis/wf-human-variation/sup/HG002/PAW70337/output/SAMPLE.wf_mods.1.bedmethyl.gz
[hg002-hp-alignment]: https://ont-open-data.s3.amazonaws.com/giab_2025.01/analysis/wf-human-variation/sup/HG002/PAW70337/output/SAMPLE.haplotagged.cram

## Preflight behaviour

The estimate rows are advisory: `preflight_resources()` guards estimation and
reporting against `ValueError`, `OSError` and subprocess failures, warns that
the resource estimate is unavailable, and lets the command continue. The
[host requirement](#whole-genome-host-requirement) and existing dependency,
memory and disk guards enforce their own policies.

These are conservative estimates, **not reservations, memory limits or
guarantees**. When size or free-space measurement is unavailable the row says
`UNKNOWN`, never a misleading `OK`. Regional disk estimates deliberately retain
whole-input upper bounds rather than guessing coverage. CPU models do not
describe pbmm2 pipelines; Parabricks has its own memory check
([above](#nvidia-gpus-parabricks)), and [hardware and acceleration](hardware.md)
covers GPU alignment.

## GUI/API integration

There is deliberately no second command-execution/dry-run path. A GUI can call
the same read-only function used by the CLI:

```python
import json
from wgsextract_cli.core.resource_estimates import estimate_resources

estimate = estimate_resources(
    "align",
    input_paths=["sample_R1.fq.gz", "sample_R2.fq.gz"],
    output_dir="results",
    reference="hs38DH.fa.gz",
    aligner="bwa",
    threads="6",
    memory="256M",
    reads=679_639_896,  # individual reads, not pairs
    output_format="CRAM",
)
print(json.dumps(estimate.to_dict()))
```

The function only inspects file metadata, reference index sizes/FAI, RAM and
free disk. It neither creates output directories nor executes alignment/indexing
tools. The caller supplies resolved reference/aligner choices; the CLI still
validates dependencies normally. A GUI may supply `temp_dir`, and `realign`
callers set `extract_fastq=True` only for newly extracted reads. Existing input
files and reused FASTQs are not charged again.

`to_dict()` returns `schema_version: 1`, `operation`, `notes`, and `resources`.
Each resource has `resource` (`memory` or `disk`), `estimated_bytes`,
`available_bytes`, `status`, `components` (byte counts) and `paths`.
Unavailable sizes are JSON `null`. `report_resources(estimate)` is the optional
logging presentation layer.

The GUI/API estimator remains strict: invalid settings raise `ValueError` so
clients can validate them. CLI callers use
`preflight_resources(lambda: estimate_resources(...))` instead.

For alignments, callers can obtain `reads` using the existing bounded
`core.alignment.read_counts.fastq_input_reads` or indexed
`core.warnings.indexed_read_count`. Omitting it leaves the D3 model's explicit
680-million-read RAM fallback; disk uses input bytes and format (12 bytes/read
for compact CRAM, 80 for BAM, 70 for FASTQ). A missing reference uses the human
reference RAM fallback, and adds a structured `UNKNOWN` disk row with no guessed
filesystem for reference-index storage.

## Calibration and limitations

Native short-read preflight checks available, not installed, RAM on Linux,
macOS and Windows. It includes resident indexes, working buffers, samblaster,
per-thread sort plus overhead and pipeline bookkeeping. Read-dependent
headroom scales with input count, not reference length. FASTQ counts use
size and a bounded prefix (up to 4 MiB compressed/16 MiB decoded per mate,
plain/gzip/BGZF); realign uses indexed `idxstats` before extraction.
Unreadable/unsupported samples or missing indexes use the explicit
680-million-read fallback, not a guarantee for larger inputs.
Changing length/compressibility later in a FASTQ can affect extrapolation.
Swap/pagefile does not count as available RAM.

Component and per-read spill estimates are logged. Low spill space warns
for align; realign retains its refusal. Auto falls back to BWA if MEM2
cannot fit; explicit MEM2 fails before alignment/extraction, including when
its proposed native MEM3 stand-in does not fit. BWA warns and continues.
Free RAM, lower `--memory`/`--threads`, or choose `--aligner bwa`.
In a 339.8-million-pair genome, BWA's working set was 17–17.5 GiB including
paged-out memory: the resident peak alone is not the requirement.
At six threads/256M, the historical conservative budgets were ~18 GiB BWA
and ~29 GiB MEM2 including transient headroom; smaller inputs scale down,
deeper/shorter-read inputs up. Newer platform/read-length calibration below
also applies.

- Ordinary 150-bp, up-to-six-thread alignment RAM retains the #179/#194 model:
  resident reference indexes, aligner working set, read-scaled samblaster and
  transient overhead, and threads × sort memory plus 25%.
- BWA-MEM working memory also accounts for platform, sampled mean read length
  and threads. FASTQ count and mean length come from the same bounded prefix;
  BAM/CRAM preflight does not decode the input to measure read length.
  Unknown lengths use 150 bp for ordinary short reads and 300 bp for Ultima.
  GUI/API callers can supply `platform="ultima"`, `read_length=300` and
  `samblaster=False` for single-end Ultima inputs.
- At fixed `-K 100000000`, Ultima allows 4.5 GiB of working memory at two
  threads and 10 GiB at four threads (300 bp), **in addition to** the index,
  sort and pipeline overhead. It scales with read length and populated batch,
  with a 2 GiB floor. This conservatively covers HG008/hs38DH: BWA peaked at
  9.18 GiB at two threads; four-thread anonymous memory reached 14.70 GiB
  before the 16 GiB unit OOM. The two-thread diagnostic timed out: this is a
  memory observation, not completed full-genome correctness. For 313,736,048
  reads and 256M sort memory, estimates are approximately 12.0 GiB at two
  threads and 18.1 GiB at four. Longer ordinary reads and more than six BWA
  threads also increase the allowance. BWA-MEM2 calibration is unchanged.
- Available RAM honors Linux cgroup-v2 ancestor caps, including reclaimable
  inactive file cache; Windows/macOS retain native available-RAM reporting.
  Namespace/subtree mounts retain their visible descendant limits.
  LOW-memory BWA warnings recommend a lower thread count plus the resolved
  `--memory` value, keeping the modeled per-sort-thread allowance fixed.
  **No automatic thread cap is applied:** requested threads and BWA arguments
  remain unchanged. Existing BWA advisory and explicit BWA-MEM2 refusal
  policies remain. Read complexity, flow-tag volume and tool versions can
  change peaks; estimates are not allocation guarantees.
- Phase-2 H2 processed **679,639,896 reads** (339,819,948 pairs). Its compressed
  FASTQ inputs totalled **47.84 GB**, and its binned-quality output was only
  **8.63 GB CRAM**, plus a 1.14 MB CRAI. Do not extrapolate that unusually good
  compression to full-quality inputs.
- The conservative disk envelope uses **120 bytes/read sort spill**,
  **28 bytes/read CRAM**, **100 bytes/read BAM**, and **100 bytes/read extracted
  FASTQ**. At H2 depth this is **81.56 GB temporary sort space and 19.03 GB
  CRAM** (75.96 and 17.72 GiB), within the phase-2 planning ranges of 70–90 GB
  sort space and about 16–20 GB CRAM. Output indexes add 1% (at least 64 KiB).
  Existing reference indexes cost no new disk; missing ones receive an
  additional two-resident-index-size build allowance on the reference volume.
  The FASTQ allowance was raised from 75 after #722's full-quality HG002
  slice wrote 937.4 MB for 10,971,483 indexed records (85.4 bytes/record):
  the files were 14% larger than the old budget. Known indexed counts avoid additional
  uncertainty from BAM/CRAM compression-based count inference.
- The full-quality HG002 **35x** run on a 16 GiB M1 (#722, `wgse-bwa`
  0.12.1/Metal, four threads, automatically selected 512M sort memory) adds
  whole-genome evidence to that slice calibration. Its 835,260,805 indexed
  input records produced **71.11 GB extracted FASTQs** (85.1 bytes/record)
  and a **46.38 GB BAM** (55.5 bytes/record). Sampled scratch including the
  FASTQs peaked at 134.86 GiB, and scratch plus results at 166.15 GiB.
  The initial advisory disk envelope was 259.81 GiB with missing reference
  indexes; the normal realignment refusal check required about 204 GiB.
  With the existing input and reference already on disk, 210.36 GiB free
  was enough, but only 37.42 GiB remained at the observed minimum. These
  different envelopes are not allocation guarantees or equivalent measures.
  Retain the conservative disk coefficients and additional free-space margin.
  [The historical measured stage table](apple-silicon-16gb.md#historical-metal-run-macos-aligners-16-and-mimalloc)
  records the exact run and resume conditions.
- **macOS RSS is not the uncompressed working set.** That run's startup RAM
  estimate was 22.02 GiB against 10.80 GiB available. The largest process
  peaked at 8.30 GiB RSS and the sampled concurrent pipeline at 10.11 GiB,
  but 1,113.72/1,116.92 GiB of system-wide swap-in/out traffic coincided with
  resumed alignment (`vm_stat` can't attribute it to a process; other desktop
  apps were open), with sampled occupied swap up to 3.70 GiB and warning
  memory pressure. Completion under compression/swap does not validate an
  8–10 GiB working-set budget. This heavy swapping is tracked as product bug
  [#747](https://github.com/WGSExtract/wgsextract-cli/issues/747): LOW memory
  predicts the problem, but defaults do not yet adapt to avoid it.
  The conservative RAM model is unchanged.
- Ultima pipelines using GATK flow duplicate marking pass
  `duplicate_marking="gatk-flow"`. Their temporary budget includes retained
  sorted/marked BAMs: up to two BAMs (200 bytes/read) for CRAM output, rather
  than assuming only sort spill and final CRAM coexist.
- bcftools compressed VCF output allows **1.6 bytes per inferred read for SNPs**
  and **0.4 for indels**, at least 64 KiB. Without a supplied read count, BAM
  and CRAM bytes are normalized using the existing 80/12 bytes-per-read
  fallback, so input compression does not directly set the VCF budget.
  The CLI supplies the cheap indexed BAM count; CRAM keeps the fallback
  rather than decoding the file for preflight.
  Serial calling (`--no-parallel`, one thread, an explicit region, or a
  caller-resolved serial fallback) writes directly to the staged output and
  reserves **no part/concatenation temporary space**.
  Parallel calling uses the runtime's shared batching plan, not one part per
  reference contig. Compressed batch headers are estimated from the complete
  reference dictionary and supplied alignment header, with compression and
  metadata headroom. Non-replaying indels reserve two output-sized data sets.
  Parallel SNPs also reserve possible deep-site split/replay files: a full
  uncompressed header for each split contig, both window and whole-contig
  fallback splices, and reheadering, plus an eight-output-sized record-data
  envelope. These files can coexist until publication, even though a shallow
  run may not need replay. All of these files are charged to the output volume,
  where the caller actually creates them.
  The CLI supplies the resolved `bcftools_contigs` and `alignment_header`;
  read-only API clients without that metadata conservatively use the reference
  dictionary. If neither a reference index nor resolved contigs are available,
  parallel temporary disk is reported unknown rather than assuming a serial run.
  On #722's nominal-30x HG002
  slice, the 670.5 MB BAM produced 2.95 MB SNP and 0.80 MB indel VCF.gz files;
  the old tenth-of-input output estimate was 23×/84× high. The new budgets
  retain several-fold headroom; depth, variant burden and compression vary.
  The subsequent whole **35x** run produced 180.70 MB SNP and 47.75 MB
  indel VCF.gz files from 835,260,415 realigned records. With the existing
  result directories subtracted, the sampled incremental **temporary plus
  new output** peaks were 0.497/0.056 GiB on the output volume, respectively.
  These are combined peaks, not separate measurements of temporary files.
  They support keeping the compressed-output budgets conservative rather
  than replacing replay/header worst-case allowances with one run's peak.
  GATK retains 10% of input bytes for output and twice that for temporary
  storage. Regional GATK CRAM conversion also budgets a whole-input BAM
  upper bound on the output volume. GATK 4.6.2.0 can read indexed CRAM 3.0
  directly when `--region` selects whole contigs and the CRAI is compatible.
  Partial intervals, other CRAM versions, and unqualified GATK builds retain
  regional BAM staging.
  GATK scattering additionally charges retained shard VCFs and their indexes to
  caller staging (`--tmp-dir` when configured, otherwise the output volume).
  JVM temporary storage has its own demand (`--tmp-dir`, otherwise system temp).
  With `--reference-evidence gvcf` the gVCF (GQ bands 10,20,30,60) is
  budgeted at 3% of the input's BAM-equivalent bytes (its read count, so a
  CRAM of the same reads gets the same budget), three times over for its
  partition parts, gathered copy and published file (2.6% of the BAM
  measured on HG002 chr20, #863). One copy is charged to the output volume
  and the other two to caller staging, combined when they share a filesystem.
  GenotypeGVCFs runs after HaplotypeCaller, within its memory.
- Sort RAM is threads × `--memory` plus 25% and 256 MiB overhead; conversion
  allows 64 MiB per codec thread plus 256 MiB overhead. bcftools allows 1 GiB per
  active contig worker plus 256 MiB. With more than one thread, contigs
  are called in parallel workers by default (`--no-parallel`, `parallel=False`
  in the API, calls them in one stream), capped by reference contig count and
  one worker for an explicit region. GATK uses a **6 GiB
  heuristic per worker**, not a claim about its Java heap limit. The opt-in
  `vcf gatk --scatter` flow-mode path selects at most `--threads` workers and
  one worker per 6 GiB of currently available physical RAM, then supplies that
  count as `gatk_workers` to the reusable estimator. Each parallel JVM has an
  explicit 4 GiB heap and one native PairHMM/GC worker; the remaining allowance
  covers non-heap memory. Unknown RAM or room for only one worker retains the
  original unscattered command. This is a conservative concurrency policy, not
  a reservation or hard RSS cap. These non-alignment models
  are conservative heuristics, not measured guarantees for every caller, read
  length or depth.

The [Linux allocator comparison](hardware.md#bcftools-with-mimalloc)
([#966, bcftools allocator measurements](https://github.com/WGSExtract/wgsextract-cli/issues/966))
does not change these budgets. On a 1.560% HG002 30× coordinate slice with
two workers, largest-child peak RSS was 284/286 MiB for SNP/indel calling
with glibc, versus 318/340 MiB with mimalloc. Across the slice and whole
fake-genome runs, sampled concurrent pipeline RSS stayed below 0.91 GiB
and sampled temporary-plus-output disk below 8.6 MiB. These are small-input
observations, not whole-genome bounds or grounds for reducing the conservative
1-GiB-per-worker allowance. Neither caller reached the 5% median speedup
gate; Linux retains the system allocator.

The [native CUDA PairHMM measurement](hardware.md#experimental-native-cuda-pairhmm-for-gatk)
does not change GATK's budgets. On public HG00096 across all mapped
reference intervals, the one-thread CUDA caller with a 4 GiB heap sampled
about 2.01 GiB peak RSS and 338 MiB peak GPU process memory. An 8 GiB
RAM lease and 64 GiB disk reservation covered the run, with about 1.55 GiB
of compressed calls and indexes. These are observations for one sample and
host, not minimum requirements, hard limits, or a reason to reduce the
estimator's allowance.

### Native DeepVariant

`wgse-deepvariant` on Apple Silicon and Linux x86-64 has a separate
sequential-stage model, not the TensorFlow partition model below.
It allows a fixed base plus per-thread make_examples
memory, batch-scaled GPU inference, and serialized whole-run records plus
one parsed contig for postprocessing. RSS alone does not capture Metal's
unified-memory footprint.

Calibration in [#721](https://github.com/WGSExtract/wgsextract-cli/issues/721):
**0.1.1**, six threads, batch 512, HG002 35x chr20 on a 16 GiB M1 peaked at
**6.56 GB inference footprint**, with **0.843 GB workspace** from a 1.09 GB
BAM. The temporary-disk allowance is shared by regional and whole-genome runs:
**0.8 temporary bytes per input byte**.
An earlier **0.1.0** whole-genome run used only 17.3 GB of workspace from a
46.0 GB BAM, but lowering the shared allowance to that ratio would underbudget
chr20. Its largest stage RSS was 3.70 GB, while Metal inference's measured
peak footprint was **5.26 GB**. At batch 512, the inference-stage RAM envelope
is **7 GiB** (1 GiB base plus 512 × 12 MiB), plus **1 GiB driver headroom**:
**8 GiB total preflight RAM**, covering the footprint rather than RSS alone.
The existing envelopes remain conservative.

On Linux x86-64 (#719), call_variants peaked at **1.5 GiB host RAM** at batch
256 on either CUDA or the CPU, also within the shared native RAM envelope.
See the [Linux/CUDA measurements](../native/deepvariant/README.md#linux-x86-64-and-nvidia-gpus-cuda)
for inference backend, GPU memory and qualification details.

The [16 GB benchmark](apple-silicon-16gb.md#native-deepvariant) projects
0.1.1 whole-genome wall time from measured 0.1.0 whole-genome time and the
0.1.1/0.1.0 chr20 timing ratio. It is not a measured 0.1.1 whole-genome run;
the estimator does not promise a wall time.

#### Apple Silicon CPU hill-climb

Measured 2026-11-19 on an M1 Pro (eight P cores, two E cores, 32 GiB),
HG002 NovaSeq 35x chr20, ten workers, Metal FP32 batch 1024, gVCF enabled.
The exact self-contained **0.4.1** superbuild, linking only macOS system
libraries, was compared with released **0.4.0**. Three unprofiled
make_examples observations per build gave median wall **137.62 → 122.84 s**
(10.7% less) and CPU **1,272.61 → 1,148.23 s** (9.8% less).

One complete staged observation per build, excluding CLI overhead:

| Stage | 0.4.0 wall s | 0.4.1 wall s | 0.4.0 CPU s | 0.4.1 CPU s | Peak RSS GB, 0.4.0 / 0.4.1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| make_examples | 136.93 | 124.95 | 1,283.43 | 1,140.75 | 0.458 / 0.469 |
| call_variants | 101.58 | 103.34 | 33.06 | 34.31 | 4.400 / 4.883 |
| postprocess_variants | 13.86 | 13.71 | 13.75 | 13.62 | 0.811 / 0.814 |

The stage sum was **252.37 → 242.00 seconds** (4.1% less), not a 10.7%
whole-pipeline speedup. Inference and postprocessing are unchanged.
CPU means user + system; memory figures are decimal GB, not GiB.
Inference's GPU-inclusive footprint was **13.679 / 14.025 GB**; RSS alone
still understates it. The two runs wrote **745.466 / 745.655 MB** of gzip
examples across ten shards and peaked at **1.097 / 1.094 GB** of allocated
intermediate space, including reference evidence.

All **210,390 VCF calls** match in sites, alleles, GT and FILTER, as do
**1,743,980 gVCF records** and all **1,533,590 complete reference-block rows**.
The [native build notes](../native/deepvariant/README.md#apple-silicon-cpu-hill-climb-041)
record the isolated changes, matched development-build repeats, and
8/10/12-worker pilots; those pilots do not justify a new thread default.
Protocol and results are tracked in
[#1116 (native Apple Silicon hill-climb)](https://github.com/WGSExtract/wgsextract-cli/issues/1116).

Whole-genome confirmation on **2026-11-20** used HG002 NovaSeq PCR-free 35x,
the same M1 Pro, ten workers, Metal FP32 batch 1024 and gVCF, one run per
version. These are measured whole-pipeline results, not chr20 projections:

| Measurement | Released 0.4.0 | Candidate 0.4.1 |
| --- | ---: | ---: |
| Wall seconds | 8,831.4 | 7,984.3 |
| CPU seconds, user + system | 50,708.6 | 46,455.5 |
| Peak RSS, GB (`time`) | 11.95 | 13.32 |
| Peak temporary space, GB (sampled every 5 s) | 29.4 | 29.5 |
| Swap growth, bytes | 0 | 0 |

Wall time fell **9.6%**, CPU time **8.4%**; RSS grew approximately **1.4 GB**.
Every non-header column is exactly equal across **7,709,239 VCF records**
and **72,210,019 gVCF records**, including **64,500,780 entire reference
blocks**, verified by matching full-body SHA-256 hashes. This compares the
two native versions, not Google's image. See the
[raw whole-genome result](https://github.com/WGSExtract/wgsextract-cli/issues/1116#issuecomment-7042830878).

The conservative RAM/disk coefficients are unchanged: the batch-scaled
inference envelope must still cover Metal buffers, not RSS alone, and gVCF
needs its additional temporary-space allowance. Peak temporary space excludes
the input BAM, reference and final outputs. One run per version on the
32 GiB M1 Pro does not establish other machines' performance or memory fit.
**The 16 GB base M1 is not yet qualified for 0.4.1**; whole-genome
memory/swap confirmation there remains follow-up. The older version-labelled
measurements above do not qualify these changes on that machine.

### DeepVariant postprocess_variants

DeepVariant's stages run one after another, so its RAM estimate is the largest
of TensorFlow inference (`2 + 14 × batch_size/1024` GiB on x64, 30 on ARM),
`make_examples` shards (1 GiB each on x64, 2 on ARM) and `postprocess_variants`,
plus 1 GiB of driver headroom.

`postprocess_variants` splits the calling regions into partitions (DeepVariant
1.9+ `--num_partitions`, at least `--cpus`) and sorts each partition's
call_variants records in memory, one partition per worker process. The
estimator replays DeepVariant's own `partition_calling_regions` on the
reference `.fai` and the `--region` selection, then charges each worker for its
partition's bases:

- 768 MiB per worker, plus
- 4.75 records per kilobase × 2 KiB per record.

Calibration (#173, HG008 Ultima ~30x on hs38DH, `--threads 5`): DeepVariant's
five partitions held 2.93, 3.01, 3.10, 3.30 and 1.06 million call_variants
records (13.4 million in total; 4.70 per kb at most, in chr13–chr20). Three
workers were OOM-killed mid-sort under a 12 GB cap at 3.39, 4.49 and 6.14 GB
anonymous RSS, which are lower bounds on their peaks. The model gives 7.1 GiB
per worker, about 36 GiB for the five, so the whole genome at `--threads 5`
needs 37 GiB rather than the 17 GiB that inference alone suggests. Density
was measured on one 30x Ultima genome; deeper or noisier inputs and other
models can produce more candidates. A chr20 replay with 16 shards peaked at
0.6 GiB per worker, consistent with the base allowance.

Before running, `vcf deepvariant` (CPU, not WES, which DeepVariant
postprocesses in one sequential pass) bounds this step by the RAM available
now, capped by `docker info`'s engine memory, such as Docker Desktop's VM:

1. If `--threads` workers fit, the command is unchanged.
2. Otherwise it keeps every worker and passes
   `--postprocess_variants_extra_args num_partitions=N` with the fewest
   partitions that make the largest one fit, logged at INFO. More partitions
   cost runtime: each worker rereads all call_variants outputs.
3. If no partition count up to 128 fits, it also lowers
   `--postprocess_cpus`, with a warning.

`num_partitions` needs DeepVariant 1.9 or later. It is used with the pinned
default images, image tags of version 1.9 or later, and other images or a
local `run_deepvariant` whose `postprocess_variants --helpfull` lists it (asked
only when more partitions are wanted). Otherwise step 2 is skipped: only
`--postprocess_cpus` is lowered, with a warning. That saves little: fewer
workers sort bigger partitions, so a whole genome still needs about 30 GiB.

With `--reference-evidence gvcf` each worker also writes its partition's part
of the gVCF, streaming `make_examples`' reference records: HG002 35x chr20 as
one partition peaked at 1.14 GiB with the gVCF and 1.14 without (#863), so the
plan is the same. `wgse-deepvariant`'s `postprocess_variants` differs: it
keeps every contig's calls until it writes the gVCF. HG002 35x chr20 and
chr22 together peaked at 0.94 GiB with the gVCF and 0.81 without, chr22 adding
3.5 bytes per base beyond its serialized records; the estimate charges 5 more
bytes to every called base (about 20 GiB for hs38DH, against 5 GiB without
the gVCF), 0.4 more temporary bytes per input byte for the gVCF records
`make_examples` writes (0.26 measured), and a gVCF of 5% of the input (1.9%
measured).
When even that cannot fit, the refusal suggests DeepVariant 1.9+.
4. If one worker cannot fit, it refuses before `make_examples` starts and
   gives the memory needed and available.

The plan uses memory available at the start; other jobs that start later can
still take it. In a container, an OOM kill of any process (from a Docker memory
cap or a host out of memory) is reported by `docker events`. The command then
kills the container and fails with an actionable error instead of letting
DeepVariant's worker pool wait forever on its lost worker. A local
`run_deepvariant` on `PATH` has no such watcher. On any failure, the stages'
work stays in `.deepvariant.work` in the output directory. A rerun with the
same settings skips the completed stages and finished `make_examples` shards,
so after freeing memory it restarts at postprocessing (see
[resuming long workflows](step-resume.md)).

Compression, read lengths, depth, extra flow-specific stages and tool versions
can change these costs. Missing reference-index construction has its own
existing tool-specific guards; the alignment RAM row describes mapping, not
index construction. No swap/pagefile is counted as available RAM.
