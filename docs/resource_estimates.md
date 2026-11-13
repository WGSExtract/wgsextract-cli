# CLI resource preflight and reusable estimates

## Imputation

`vcf impute` prints estimated memory and runtime before starting. HG002 chr20
measurements at four threads: GLIMPSE2 phase/ligate 431–464 s and 2.2–3.1 GB
peak RSS; Beagle 60 s and 3.4 GB. The memory preflight reserves a conservative
4 GiB for GLIMPSE2 and 8 GiB for Beagle. Sequential chromosomes/chunks keep
memory bounded; runtime scales with selected reference bases and thread count.
The extrapolated whole-autosome GLIMPSE2 time is about 5.5 h at four threads,
or 2.8 h at eight, excluding one-time panel preparation. These are not measured
whole-genome runtimes. See [imputation](imputation.md) for accuracy and limits.

## Other pipelines

Native `align` (BWA/BWA-MEM2 and minimap2), `realign`, `bam sort`, `bam to-bam`,
`bam to-cram`, and `vcf snp`/`indel`/`gatk`/`deepvariant` (CPU, every platform)
report estimates before starting their heavy pipelines. For example:

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

`LOW` warns. Existing refusal policies are unchanged: explicitly selected
BWA-MEM2 refuses inadequate RAM, BWA warns, auto can fall back to BWA, and
realignment/whole-file sorting retain their existing disk guards. `--no-preflight`
still bypasses realignment's disk refusal, not its memory guard; informational
estimates are still printed. Realignment reports before FASTQ extraction and
alignment reports again when the final aligner is selected.

The memory estimate is the selected aligner's. On Linux, bwa-mem3 standing in
for bwa (`--compat=bwa-mem`, chosen first since #736) needs about twice bwa's
RAM (5M pairs on hs38: 14.7 GiB peak at 16 threads against bwa's 6.6 GiB;
`wgse-bwa --cuda` peaked at 6.3 GiB at 8 threads), so it is taken only when its
pipeline fits the RAM available; otherwise `wgse-bwa` (on its GPU when that
fits) or bwa runs, and the preflight reports that aligner's estimate
([hardware guide](hardware.md#bwa-mem3-on-linux)).

Eligible paired BWA/bwa-mem2 realignment without `--keep-fastq` streams newly
extracted reads (forced refresh of cached FASTQs at the exact output paths
retains file handling): `samtools view | collate | fastq` writes interleaved
pairs to stdout and the aligner reads them with `-p`, through ordinary process
pipes on every platform, including native Windows. There is no
`extracted_fastqs` demand. minimap2, GPU and long-read workflows keep files.

Streaming uses file extraction's collate bucket count (20× BAM / 70× CRAM input
size against threads × per-thread memory, 64–200 buckets). Collate's output
order depends on that count, so the aligner receives the same pairs in the same
order either way and the realigned records are identical. A 30x genome streams
whenever one bucket fits beside alignment in RAM.

Collate holds one bucket in memory while BWA maps. Its estimate is the largest
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
of a broken pipe (#781). `--memory`, including `auto`'s floor of 1 GiB per
thread, can ask for more. So `align`, `realign`, `pet`, `bam sort` and
`bam to-ubam` give each sort the smallest of:

- `--memory` per thread;
- what its read files can fill: 96 bytes of records per byte of input (the
  bound minimap2's estimate uses below), split across the threads, when every
  input is a local file;
- half of this computer's RAM, split across the threads,

and logs when that lowers `--memory`. The input bound never goes below 16M per
thread, but half the RAM is a hard limit, down to samtools' minimum of 1M. Records and their order are unchanged: a sort that
fits its input sorts in memory, and a sort that spills merges its spills stably.
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
| Output CRAM / BAM | 1.7 / 2.6 | slice CRAM 1.90 (1.91 bases per base); whole genome 1.79 (#431) |
| Tagged bgzipped FASTQ (`realign`) | 1.45 | 1.39 |

`realign --tmp-dir` on another volume needs the spills there, and the output and
FASTQ in the output directory. For the 103.7 GB HG002 ONT CRAM at `--threads 5
--memory 256M` that is about 316 GiB of scratch and 164 GiB of output plus 141
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
only for the WGS model, when it is installed, `deepvariant_weights` is set and
`deepvariant_native` isn't `off`; WES, PacBio and ONT still run Parabricks, and
Ultima Google's GPU image, with the memory needs above. This pipeline runs only
call_variants on the GPU and
checks just before it that GPU 0 has 3 GiB free (`--no-gpu-memory-check`
skips this); make_examples and postprocess_variants stay on the CPU. Its RAM
estimate is the Mac build's (#647), which covers what Linux used.

Measured (#719) on a Ryzen 5 5600X with an RTX 3090, `--threads 4`, from the
`linux-deepvariant-1` package, on HG002's 35x NovaSeq BAM (GRCh38 no-alt), chr1
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

`vcf clair3 --gpu` runs HKU's Clair3 2.0.5 GPU image and checks only that a
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

On a whole genome the GPU mode needs much more disk than the CPU mode: it
writes every full-alignment tensor before predicting any (see below).

## Whole-genome ONT requirements

Measured on the whole HG002 ONT R10.4.1 CRAM (103.7 GB, about 51x, hs38;
[#431](https://github.com/WGSExtract/wgsextract-cli/issues/431)). Details,
accuracy and caveats are in
[ONT whole-genome requirements](ont.md#whole-genome-ont-requirements).

| Step | Host, threads | Wall time | Peak RAM | Disk |
| --- | --- | ---: | ---: | --- |
| `realign`, chunked, streamed from the URL, `--memory 512M` | Apple M1 16 GB, 7 | 21.4 h | 10.5 GB | 185.5 GB output (1.79x input); preflight asked for about 211 GB |
| `vcf deepvariant --ont`, Google's CPU image | Ryzen 5 5600X, 10 | 11.3 h | not recorded | under 150 GB work directory; plus a CRAM 3.0 copy of CRAM 3.1 input |
| `vcf clair3 --gpu` | Ryzen 5 5600X + RTX 3090, 6 | 2.5 h | 15.2 GiB, GPU 6.1 GiB | about 100 GiB temporary (full-alignment tensors, about 1 GiB per 1% of the genome) |
| `rtg vcfeval` of one whole-genome VCF | 4 | 1 min | 7.2 GB | |

Clair3's temporary space isn't checked before it starts yet
([#811](https://github.com/WGSExtract/wgsextract-cli/issues/811)).

## Preflight behaviour

Preflight is advisory: `preflight_resources()` guards estimation and reporting
against `ValueError`, `OSError` and subprocess failures, warns that the resource
estimate is unavailable, and lets the command continue. Existing dependency,
memory and disk guards still enforce their own policies normally.

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
`core.read_counts.fastq_input_reads` or indexed
`core.warnings.indexed_read_count`. Omitting it leaves the D3 model's explicit
680-million-read RAM fallback; disk uses input bytes and format (12 bytes/read
for compact CRAM, 80 for BAM, 70 for FASTQ). A missing reference uses the human
reference RAM fallback, and adds a structured `UNKNOWN` disk row with no guessed
filesystem for reference-index storage.

## Calibration and limitations

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
  [The measured stage table](apple-silicon-16gb.md#current-defaults-macos-aligners-16-and-mimalloc)
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
  storage. Regional GATK CRAM conversion
  also budgets a whole-input BAM upper bound on the output volume.
  GATK scattering additionally charges retained shard VCFs and their indexes to
  the **output volume**, where they coexist with the gathered output; JVM
  temporary storage remains a separate demand on the system temporary volume.
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
