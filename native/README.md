# WGS Extract's aligner rebuilds

These builds are for Apple Silicon. `wgse-bwa` is also built for Linux x86-64,
with an experimental NVIDIA GPU backend (see
[Linux x86-64 and NVIDIA GPUs](#linux-x86-64-and-nvidia-gpus-cuda)).

Optional macOS arm64 (macOS 13+) rebuilds of the aligners WGS Extract uses,
published as `macos-aligners-*` prereleases. `wgsextract align` and `realign`
run them in place of their upstream aligners when they're on `PATH`, and
`wgse-bwa-mem3` with `--compat` in place of bwa or bwa-mem2 (see
[hardware and acceleration](../docs/hardware.md#apple-silicon-metal)).

[`deepvariant/`](deepvariant/README.md) builds `wgse-deepvariant`, an Apple
Silicon build of DeepVariant 1.10.0, published separately as
`macos-deepvariant-*` prereleases.

| Executable | Upstream | Notes |
| --- | --- | --- |
| `wgse-bwa` | BWA 0.7.19 | Native arm64 build. CPU by default (upstream code, with some steps replaced by exact faster versions); `--metal` is **experimental**. Low memory: about 6 GB for a human index. |
| `wgse-bwa-mem2` | BWA-MEM2 2.3 | arm64 build through sse2neon. CPU by default (upstream code, with some steps replaced by exact faster versions); `--metal` is **experimental**. Needs much more memory than BWA (see below). |
| `wgse-bwa-mem3` | bwa-mem3 0.16.0 | Self-contained arm64 build of bwa-mem3, a faster BWA-MEM2 descendant that reads the BWA-MEM2 index and can write BWA's or BWA-MEM2's exact records (`--compat`). CPU by default; `--metal` is **experimental**. |
| `wgse-minimap2` | minimap2 2.31 | arm64 build with a NEON base-level alignment kernel and faster index loading and building, seeding and anchor sorting on the CPU. `--metal` (long-read presets) is **experimental**. |

All write the same alignments as their upstream release (in each of
bwa-mem3's output modes), with or without `--metal`; only the `@PG` version
gains a `-wgse-<version>` suffix (and its command line records any backend
option). Each package has its own version,
printed by `--version`.

## Installing

The `macos-aligners-16` (Apple Silicon) and `linux-aligners-2` (Linux x86-64
and ARM64) prereleases have `wgse-bwa` 0.12.1; `linux-aligners-2` also has
`wgse-bwa-mem3` 0.4.0 for Linux x86-64. The steps below install the
Apple Silicon executables; on Linux, see
[Linux x86-64 and NVIDIA GPUs](#linux-x86-64-and-nvidia-gpus-cuda). Earlier published `wgse-bwa`
executables, 0.11.0 and 0.12.0, advertise dense suffix-array support but reject
`--dense-sa`. The CLI omits that option for those versions, so alignment works
when no dense side file is present. With a side file it refuses: those builds
read it automatically and cannot honor the run's opt-in setting. Upgrade to
0.12.1, or remove the side files with
`wgsextract ref dense-sa --ref <fasta> --delete`.

The `macos-aligners-16` prerelease has `wgse-bwa` 0.12.1, which parses
`--dense-sa on|off` in release builds, with the same `wgse-bwa-mem2` 0.5.2 and
`wgse-minimap2` 0.3.0 as `macos-aligners-15`, and `wgse-bwa-mem3` 0.3.1, whose
`--metal` finishes a run on the CPU after a GPU seeding batch fails and then
exits 1, as after an extension failure.
The `macos-aligners-15` prerelease has `wgse-bwa` 0.11.0, which can use an
optional dense suffix-array side file (`wgse-bwa densify-sa`), `wgse-bwa-mem2`
0.5.2, `wgse-bwa-mem3` 0.3.0, whose `--metal` also collects SMEMs on the GPU,
and `wgse-minimap2` 0.3.0, with faster CPU and `--metal` gap fills. All four
fall back to the CPU when a GPU command hangs (GPU watchdog) and align exactly
as before.
The `macos-aligners-14` prerelease has `wgse-bwa` 0.9.0 and `wgse-bwa-mem2`
0.5.0, whose CPU backends are faster and align exactly as before, with the
same `wgse-bwa-mem3` 0.2.0 and `wgse-minimap2` 0.2.0 as `macos-aligners-13`.
The `macos-aligners-13` prerelease has `wgse-bwa` 0.7.1, `wgse-bwa-mem2`
0.4.1, `wgse-bwa-mem3` 0.2.0, whose `--metal` scores seed extensions on the
GPU, and `wgse-minimap2` 0.2.0, whose `--metal` runs gap fills on the GPU.
`wgse-bwa` 0.7.1 shares the updated
mate-rescue kernel with `wgse-bwa-mem2` 0.4.0 and aligns exactly as 0.7.0
does. `wgse-bwa --metal` and `wgse-bwa capabilities` need `wgse-bwa` 0.2.0 or
later. `macos-aligners-12` has the same `wgse-bwa` and `wgse-bwa-mem2` with
`wgse-minimap2` 0.1.0, which aligns on the CPU only, and no `wgse-bwa-mem3`.
`macos-aligners-11` has
`wgse-bwa` 0.7.1, `wgse-bwa-mem2` 0.4.0, which aligns exactly as 0.4.1 but
without its memory and GPU-limit warnings, and `wgse-minimap2` 0.1.0. Earlier
prereleases, without `wgse-minimap2`: `macos-aligners-10` has
`wgse-bwa` 0.7.1 and `wgse-bwa-mem2` 0.4.0; `macos-aligners-9` has `wgse-bwa`
0.7.0 and `wgse-bwa-mem2` 0.3.0, with slower GPU kernels; `macos-aligners-8`
has `wgse-bwa` 0.7.0 and
`wgse-bwa-mem2` 0.2.0, whose `--metal` loads the `.0123` file and starts GPU
seeding only when a chunk maps; `macos-aligners-7` has `wgse-bwa` 0.7.0 and
`wgse-bwa-mem2` 0.1.0, whose `--metal` only extends seeds on the GPU;
`macos-aligners-6` has `wgse-bwa` 0.6.0, whose
CPU threads locate seeds and rescue mates one at a time;
`macos-aligners-5` has `wgse-bwa` 0.5.0, with
slower GPU kernels and no overlap of mapping and pairing;
`macos-aligners-4` has `wgse-bwa` 0.4.0, whose
`--metal` also rescues mates on the GPU but extends seeds on the CPU;
`macos-aligners-3` has `wgse-bwa` 0.3.0, whose
`--metal` seeds and locates on the GPU but rescues mates on the CPU;
`macos-aligners-2` has `wgse-bwa` 0.2.0, whose `--metal` only seeds on the
GPU; `macos-aligners-1` has `wgse-bwa` 0.1.0, which aligns on the CPU only.

Download into an empty directory with `gh` (or `curl`), which avoids the
browser quarantine flag that Gatekeeper applies to these ad-hoc-signed
executables, and check the archives against the SHA-256 digests GitHub
publishes for each release asset:

```bash
mkdir wgse-aligners && cd wgse-aligners
gh release download macos-aligners-16 -R WGSExtract/wgsextract-cli
gh release view macos-aligners-16 -R WGSExtract/wgsextract-cli --json assets \
  --jq '.assets[] | "\(.digest | ltrimstr("sha256:"))  \(.name)"' |
  shasum -a 256 -c
```

Continue only if every archive reports `OK`. Then extract them and put the
`bin` directories on `PATH` (add the `export` line to your shell profile to
keep it):

```bash
for archive in wgse-*-macos-arm64.tar.gz; do tar -xzf "$archive"; done
for dir in wgse-*-macos-arm64; do PATH="$PWD/$dir/bin:$PATH"; done
export PATH
wgse-bwa capabilities --json
wgse-bwa-mem2 capabilities --json
wgse-bwa-mem3 capabilities --json
wgse-minimap2 capabilities --json
```

macOS kills a quarantined executable as soon as it starts. If an archive was
downloaded in a browser, clear the flag before running, for example
`xattr -dr com.apple.quarantine wgse-bwa-0.7.1-macos-arm64` (and likewise
`wgse-bwa-mem2-0.4.1-macos-arm64`, `wgse-bwa-mem3-0.2.0-macos-arm64` and
`wgse-minimap2-0.2.0-macos-arm64`).

## Usage

```bash
wgse-bwa index hs38.fa
wgse-bwa mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa --metal mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa capabilities --json

wgse-bwa-mem2 index hs38.fa
wgse-bwa-mem2 mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem2 --metal mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem2 capabilities --json

wgse-bwa-mem3 index hs38.fa   # or reuse a BWA-MEM2 index; .0123 is not needed
wgse-bwa-mem3 mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem3 mem -t 9 --compat=bwa-mem hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem3 --metal mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem3 capabilities --json

wgse-minimap2 -t 9 -ax map-hifi hs38.fa hifi.fastq.gz > out.sam
wgse-minimap2 -x map-ont -d hs38.map-ont.mmi hs38.fa
wgse-minimap2 -t 9 -ax map-ont hs38.map-ont.mmi ont.fastq.gz > out.sam
wgse-minimap2 --metal -t 9 -ax map-hifi hs38.map-hifi.mmi hifi.fastq.gz > out.sam
wgse-minimap2 capabilities --json
```

For `wgse-bwa`, `wgse-bwa-mem2` and `wgse-bwa-mem3`, options before the command select the
backend of `mem` (other commands ignore them):

- default (`--backend cpu`): the upstream CPU code (in `wgse-bwa` from 0.8.0
  and `wgse-bwa-mem2` from 0.5.0 with some steps replaced by exact faster
  versions, described below);
- `--metal` (`--backend metal`): fails if Metal is unusable, and exits 1 if a
  GPU batch fails (the CPU still completes the alignments, so the output is
  complete and correct);
- `--backend auto`: like `--metal`, but falls back to the CPU in both cases
  with only a note on stderr.

`wgse-bwa-mem3`'s `mem` is upstream bwa-mem3's: by default it writes bwa-mem3's records
(BWA-MEM2's, with different MAPQs and `MQ`/`HN` tags), `--compat=bwa-mem` and
`--compat=bwa-mem2` write exactly BWA's and BWA-MEM2's records, and `--fast`
trades alignments for speed. It is built with upstream's own dependencies
(htslib, libsais, mimalloc, zlib-ng, libdeflate) linked in, so it needs no
other libraries. Its `index` builds the suffix array on one thread (upstream
uses OpenMP, which Apple clang lacks); the files are the same.

From 0.8.0, `wgse-bwa`'s CPU backend also uses the exact replacements its
`--metal` CPU threads have used since 0.7.0: it locates each read's seeds with
16 interleaved suffix-array walks, aligns each pair's mate-rescue windows
together (upstream's 8-bit kernel on up to four windows of the same length at
once), sorts alignments by pointer and unpacks reference bases with NEON. Each
gives upstream's exact results; on the 5M HG002 pairs below they cut the time
by a third.

From 0.9.0, `wgse-bwa`'s CPU backend (and `--metal`'s CPU threads) extends seeds with a
NEON version of `ksw_extend2` that gives upstream's exact results whenever the
scores fit 16 bits (upstream's code runs otherwise). Its CPU backend also maps
each chunk 8 pairs at a time: it collects the SMEM intervals of all their reads
at once, each read's FM-index walk prefetching the occurrence blocks of its next
step while the other reads step, then locates all their seeds in one
interleaved suffix-array walk. Each read's seeds are upstream's, in upstream's
order. On the 5M HG002 pairs below 0.9.0 takes 27% less time than 0.8.0.

`wgse-bwa` 0.9.2 aligns exactly as 0.9.1. Its GPU scheduling no longer depends
on Metal (the Metal kernels sit behind a device interface, for the CUDA
backend), and it also builds on Linux x86-64, on the CPU.

From 0.11.0, an optional side file speeds up locate: nothing creates it
automatically, and `mem` aligns exactly as before without it.
`wgse-bwa densify-sa <index>` writes `<index>.wgse-sa16`, the
suffix-array position of every 16th row (derived from the index's own samples;
0.80 GB for hs38, `-i` picks another power of two). `mem` then locates seeds
with walks of at most 15 steps instead of 31, on the CPU and the GPU, if the
machine has at least 16 GiB and the file takes at most an eighth of it; it
checks the file against the index and otherwise ignores it. `--dense-sa off`
ignores it, `--dense-sa force` skips the memory check, and
`capabilities --json <index>` lists the files and the one `mem` would read (it
checks each file's header and checksum; only `mem` also spot-checks 1024
samples against the index). Mate rescue also steps the lazy-F loops of the
windows it aligns together in turn, and `--metal`'s CPU threads seed the pairs
they take from the GPU's queue 8 at a time with the CPU backend's interleaved
walks. Each gives upstream's exact results. WGS Extract builds the file only
with `wgsextract ref dense-sa`, and its `align` and `realign` read it only with
`--dense-suffix-array` (or `dense_suffix_array = true` in `config.toml`);
otherwise they pass `--dense-sa off` (docs/hardware.md).

From 0.5.0, `wgse-bwa-mem2`'s CPU backend does the same with its own exact
replacements: it collects each read's SMEMs in all three passes with up to 16
reads stepping in turn (prefetching each step's occurrence-table lines, and
taking intervals of strings up to 10 bases from a precomputed table), aligns
each pair's mate-rescue windows together with NEON and skips the windows
bwa-mem3 proves cannot change the result, tests the band of its 128-bit
extension kernels with NEON, splits each chunk's reads evenly between threads,
sorts alignments by pointer and decodes reference windows from the `.pac`
rather than loading the 6.2 GB `.0123` file. Each gives upstream's exact
results; on the 5M HG002 pairs below they cut the time by 44% and the peak
memory by a third. `--cpu-upstream` (a test option) runs upstream's code.

What runs on the GPU:

- `wgse-bwa`: exact SMEM seeding (BWA's three-pass FM-index interval
  collection) and, from 0.3.0, suffix-array locate of the seeds (`bwt_sa`).
  Within each chunk of reads (`-K`) the GPU prepares batches of pairs from the
  front while the CPU threads extend the prepared pairs from the cached
  intervals and positions; when none is ready, a CPU thread maps an unprepared
  pair from the back of the chunk with upstream's code. On HG002 the GPU
  prepares about 88% of the pairs. Reads the GPU cannot seed exactly (over
  512 bp, too many intervals, or seeding options wider than 32 bits) are seeded
  by upstream's CPU code; on HG002 this is 0.3% of reads. From 0.4.0 the
  pairing step works the same way: the GPU aligns every mate-rescue window
  (`mem_matesw`'s `ksw_align2`) of batches of pairs from the front of the
  chunk, and the CPU threads use those results when they finish the pairs;
  on HG002 the GPU takes about 45% of the pairs. Windows with scores that need
  16 bits, mates over 256 bp, or bases other than A, C, G, T and N are
  aligned on the CPU. From 0.5.0 the GPU also extends seeds (`mem_chain2aln`'s
  `ksw_extend2`): after a CPU thread chains a pair, it stages each chain's
  first extension (left, then right from the left score) in a batch shared by
  all threads, the GPU extends full batches, and a CPU thread finishes the
  pair, using a GPU result only when the query, reference, start score, band
  and end bonus of upstream's call match it exactly (otherwise, as for the
  other seeds of a chain, upstream's code extends on the CPU). On HG002 the GPU
  extends the seeds of about 87% of the pairs, and 98% of those pairs'
  extension calls use its results. Extensions with queries over 256 bp or scores that
  need more than 16 bits are done on the CPU. From 0.6.0 the GPU kernels for
  mate rescue and seed extension are faster, and the pairing step of one chunk
  runs while the next chunk is mapped (the GPU is the bottleneck when pairing
  and the CPU threads when mapping), so up to three chunks of reads are in
  memory at once. While two chunks overlap, mapping and pairing each run `-t`
  threads, so up to twice `-t` alignment threads (plus a few GPU helper
  threads) can be runnable; lower `-t` to leave cores for other work. Seeding and locate also count bases in the index's
  occurrence blocks with fewer GPU instructions, and once a run has mapped 20M
  reads (on Macs with at least 16 GiB of memory) the GPU derives a suffix-array
  sample for every 16th row from the index's every-32nd-row samples, which
  halves the steps of each locate on the GPU and the CPU. From 0.7.0 the CPU
  threads do their share of the work faster, so they take more of each chunk
  from the GPU, which is the bottleneck: they locate a read's seeds with 16
  suffix-array walks interleaved, align a pair's mate-rescue windows together
  (running upstream's 8-bit kernel on up to four windows of the same length
  at once; windows that need the 16-bit kernel are left to upstream), sort the rescued alignments by pointer, and unpack reference bases
  with NEON, all with upstream's exact results. At most two GPU
  command buffers of the process run at once: with more, macOS sometimes
  aborts one to keep the display responsive (`ImpactingInteractivity`). It
  can still do so when other processes load the GPU; 0.6.0 then runs the
  aborted work again, up to four times, before it finishes on the CPU (and
  `--metal` exits 1). 0.7.1 updates the mate-rescue kernel it shares with
  `wgse-bwa-mem2` 0.4.0 (less work per cell for windows without N), with no
  change in behaviour or results.
- `wgse-bwa-mem2`: batches seed extension across worker threads, keeping small
  batches on the CPU. From 0.2.0 it also collects the SMEMs of batches of 16384
  reads on the GPU (upstream's three passes, reading the index's occurrence
  table in place), rescues mates on the GPU with `wgse-bwa`'s kernel, and pairs
  one chunk while it maps the next, as `wgse-bwa` 0.6.0 does. As in `wgse-bwa`,
  the GPU takes reads from the front of each chunk and the CPU threads from
  the back, the CPU uses a GPU result only for exactly the reads and inputs it
  was computed for, reads with more than 128 SMEMs or over 512 bases are
  seeded on the CPU, and at most two GPU command buffers run at once; aborted
  GPU work is retried and then finished on the CPU (`--metal` exits 1). 0.2.0
  also stops upstream's suffix-array locate from allocating a buffer sized by
  the seeds' total number of hits for every read, and lets it prefetch
  sampled entries, which it never did because of an operator-precedence slip;
  neither changes any result.
  With `--metal`, 0.3.0 does not load the `.0123` file (the reference unpacked
  to a byte per base, 6.2 GB for hs38) and decodes extension windows from the
  `.pac` instead; the GPU starts seeding a chunk as soon as it is read, while
  the previous chunk maps, so CPU threads seed almost nothing; and CPU threads
  rescue mates in interleaved batches, sort alignments by pointer and unpack
  reference bases with NEON, as `wgse-bwa` 0.7.0 does. None of this changes
  any result.
  In 0.4.0 the GPU looks up the index interval of every string of up to 10
  bases in a 34 MB table it builds once, instead of walking the occurrence
  table for the first bases of each SMEM; its seed-extension and mate-rescue
  kernels do less work per cell; and seed extensions that fit 8-bit scores run
  on the CPU threads while their 16-bit companions are on the GPU, which
  leaves the busy GPU more time for seeding and rescue.
  GPU seeding reads the index's occurrence table (5.8 GiB for hs38) as one
  Metal buffer. If the GPU's largest buffer (`maxBufferLength`) is smaller,
  as on GitHub's virtual M1s (3.5 GiB), 0.4.1 warns on stderr and seeds on
  the CPU, while the GPU still extends seeds and rescues mates, and exits 0;
  earlier versions did the same after a "CPU fallback" message and then
  exited 1 with `--metal`.
- GPU watchdog (`wgse-bwa` 0.9.1, `wgse-bwa-mem2` 0.5.1, `wgse-bwa-mem3`
  0.2.1, `wgse-minimap2` 0.2.1): a GPU command that has run for too long
  with no error from macOS, as on a hung virtual GPU, makes the aligner stop
  using the GPU for the rest of the run. Too long means 180 s until any
  command has completed (the first may page in a large index), then 60 s or
  20 times the slowest command so far, if longer, counted from when macOS
  scheduled the command, in uptime (so a sleeping Mac does not trip it). The
  work in flight finishes on the CPU with the same results; `--backend auto`
  exits 0 and `--metal` exits 1. Earlier versions waited forever. Buffers
  and the shared index stay allocated while an abandoned command might still
  run. Whether the process then exits promptly with a command still stuck on
  the GPU is up to the Metal driver, and is untested: CI's injected hang
  never reaches the GPU.
- `wgse-bwa-mem3` (from 0.2.0): seed extension, and from 0.3.0 SMEM
  seeding, in every output mode.
  bwa-mem3 extends seeds in batches, scoring each batch first with 8-bit
  scores and then, for the extensions that overflow, with 16-bit scores; the
  GPU scores whole 8-bit batches with its own banded Smith-Waterman kernel,
  which gives the same results as upstream's NEON kernel, and the 16-bit
  batches stay on the CPU. A batch with a query over 256 bases or a base
  other than A, C, G, T and N is scored by upstream's code; on HG002 none
  was. A CPU thread waits while the GPU scores its batch, so `--metal` runs
  twice `-t` alignment threads (`--gpu-threads PERCENT`, from 100 to 800,
  sets the share; the size of a chunk of reads still follows `-t`, so the
  output does not change). A GPU command buffer that macOS aborts to keep the
  display responsive is run again, up to four times; any other GPU error,
  such as a GPU hang, sends that batch and every later one to the CPU (and
  `--metal` exits 1).
  From 0.3.0 the GPU also collects each read's SMEMs (upstream's three
  passes) with `wgse-bwa-mem2`'s seeding kernel, reading the index's
  occurrence table in place, while the previous chunk of reads maps; reads
  over 512 bases or with more than 128 SMEMs, and any batch not finished when
  its reads map, are seeded by upstream's code, and the SMEMs are then sorted,
  deduplicated and resolved as upstream does (a GPU SMEM does not carry the
  CPU's unique-match tag, so its suffix-array lookup walks to a sampled row
  for the same coordinate). With the CPU threads freed from seeding, they
  score a batch of seed extensions themselves while more than 4096 pairs wait
  for the GPU (`--gpu-backlog PAIRS`; 0 never does). `--gpu-seed off` seeds on
  the CPU as 0.2.0 did (and then `--gpu-backlog` defaults to 0). If a GPU
  seeding batch fails, the CPU seeds the rest and the run goes on (from 0.3.1,
  `--metal` then exits 1, like after an extension failure; an index larger
  than the GPU's largest buffer only turns GPU seeding off). Long extensions
  (over 30,000 band cells) are scored by a SIMD group each instead of one GPU
  thread. Seeding and seed extension share the process's two GPU command
  buffers in flight (`metal-common`), so a seeding batch waits while two
  extension batches run, and the reverse. The GPU saves about 10% of the time
  in the modes that use upstream's full seed extension (see Performance).

With a Metal backend all three print GPU and CPU work counts to stderr.
`wgse-bwa-mem2 capabilities --json [index]` (0.4.1) also reports the Mac's
memory (`physical_memory_bytes`) and the GPU's limits (`metal.max_buffer_bytes`,
`metal.recommended_max_working_set_bytes`); given an index prefix, it reports
the occurrence table and index sizes the CPU and `--metal` load, and whether
GPU seeding can use them (`index.gpu_seeding`, with the reason in
`index.gpu_seeding_limit` if not).

`wgse-minimap2` takes minimap2's options and writes the same `.mmi` indexes,
which either tool can load. By default it aligns on the CPU, with these exact
changes to upstream's code (none changes any output):

- loading a `.mmi` reads each hash bucket in one call and fills the buckets'
  hash tables on `-t` threads, in upstream's insertion order;
- building an index from a FASTA file packs the bases and collects the
  minimizers of each sequence on `-t` threads, in segments of about 4 Mbp
  that each start with 1024 bases of warm-up, which leave the minimizer
  window in the state upstream's single pass reaches (a sequence whose
  warm-up is too short or ambiguous is sketched by upstream's code);
- the `mid_occ` threshold and the index statistics are computed from
  per-thread histograms of minimizer occurrences instead of a serial
  selection;
- the in-place radix sort of anchors and chains skips byte passes in which
  all keys are equal, which leave the order unchanged;
- seed collection prefetches the hash slots and positions of the minimizers
  ahead;
- (0.3.0) the anchor radix sort replays each pass of upstream's in-place sort
  out of place, which keeps its order of equal keys (chaining depends on it);
- (0.3.0) base-level alignment (`ksw_extd2`) is written for NEON instead of
  going through sse2neon.

`--metal` (0.2.0, experimental; given before minimap2's options; 16-bit
GPU kernels since 0.3.0) runs most
base-level gap fills on the GPU, for presets whose alignment uses two affine
gap costs (`map-hifi`, `map-ont`, `map-pb` and `asm*`). Before a read is
aligned, the gap fills between its chained anchors are queued for a Metal
kernel that computes the same scores and backtrack as minimap2's
`ksw_extd2`; minimap2 then takes a GPU result only for byte-identical inputs.
To keep enough reads in flight for large GPU batches (up to 4096 fills), each
`-t` thread runs 16 reads as user-space fibers that switch while a read waits
for the GPU, sharing the thread's memory arena, and threads that run out of
reads compute queued fills themselves. When that arena outgrows minimap2's
reset limits (`--cap-kalloc`, default 500 MB), the thread starts no new reads
until its reads in flight finish, then frees it, as minimap2 does after a read. Fills longer than 512 read bases or
4096 reference bases, or that minimap2's band would narrow, run on the CPU, as
do the
largest fills (more than 8192 cells per kernel lane, about 3% of HiFi fills),
because a GPU batch takes as long as its slowest fill. The ends of each
alignment, `sr` and the splice presets stay on the CPU. `--metal` fails if
Metal is unavailable, and exits 1 if a GPU batch fails (its fills then run on
the CPU, so the output is complete and correct). It prints GPU and CPU fill
counts to stderr.

`-DWGSE_MINIMAP2_KSW2=sse41` or `sse2` builds upstream's SSE4.1 or SSE2-only
(as Bioconda does) base-level alignment through sse2neon instead of the
default NEON `ksw_extd2`; all give the same alignments, and a fuzz test
compares the NEON kernel with upstream's on random inputs. The tests build unmodified upstream minimap2 2.31 and require
identical indexes and SAM records (apart from `@PG`) for map-hifi, map-ont,
map-pb and sr on a synthetic genome with segmental duplications and tandem
repeats. On a Mac with Metal they also require the same records from
`--metal`, including with small batches, one fiber per thread, a lower cost
cap, interrupted command buffers and an injected GPU failure.

## Memory

BWA-MEM2 trades memory for speed. Building its human index needs roughly
60–90 GB of RAM, and aligning needs the 15–20 GB index in memory. BWA fits
ordinary desktops. On hs38 with 150 bp HG002 pairs, `wgse-bwa` built the index
with a 6.2 GiB peak in 100 minutes, and aligning peaked at 5.3 GiB with 1 thread
and 5.9 GiB with 9 threads (default `-K`), or about 75 MiB per extra thread.
`wgse-bwa --metal` adds about 0.4 GiB, mostly the GPU's seed output buffer;
suffix-array locate shares the index's sampled suffix array with the GPU in
place and adds about 32 MiB. Its host memory does not grow with `-K` or `-c`:
at most three prepared batches wait for the CPU threads, and each locates at
most 2^21 rows (32 MiB); the rows of further reads are located on the CPU.
Mate rescue on the GPU (0.4.0) added about 0.13 GiB on HG002: at most three
prepared rescue batches wait for the CPU threads, each with at most 64 MiB of
mate and reference bases and as much GPU scratch, and further windows are
aligned on the CPU.
Seed extension on the GPU (0.5.0) added about 0.14 GiB on HG002: at most six
extension batches of 1024 pairs, each with up to 8 MiB of bases and 4 MiB of
jobs and results; when none is free, threads extend on the CPU.
0.6.0 pairs one chunk while it maps the next, which added about 0.15 GiB on
HG002 with `-K 10000000` (the reads and alignments of the extra chunk; more
with larger `-K`). After 20M reads, on Macs with at least 16 GiB, the denser
suffix-array sample adds one 33-bit entry for every 32 rows of the suffix
array, about 0.76 GiB for hs38 (built in about 18 s). 0.7.0 does not change
the peak.
With `--metal` the GPU uses the whole index
(4.3 GiB for hs38) in place, so it stays resident while aligning; it has not
been tested on 8 GB Macs.
`wgse-bwa-mem2` peaked at 16.3 GiB on hs38 with 9 threads and `-K 10000000`
(it loads its 17.0 GB of index files), so it does not fit in a 16 GB Mac.
`--metal` 0.1.0 added under 100 MB of GPU buffers; 0.2.0 peaked at 16.9 GiB.
It reads the index's 5.8 GiB occurrence table in place, and adds GPU seeding
scratch and output for one batch of 16384 reads (about 0.17 GiB for 150 bp
reads), up to three prepared seeding batches, mate-rescue buffers like
`wgse-bwa`'s, and the extra chunk of pairing one chunk while it maps the next
(about 0.16 GiB with `-K 10000000`; more with larger `-K`).
`--metal` 0.3.0 does not load the 6.2 GB `.0123` file, so it peaked at
11.5 GiB (14.8 GiB with the default `-K`, against 19.7 GiB for 0.2.0), even
though it keeps up to eight prepared seeding batches per chunk and seeds up to
two chunks ahead. That leaves more room for other applications on a 32 GB
Mac; it has not been tested on a 16 GB Mac. 0.4.0 adds a 34 MB table of
index intervals; it peaked at 11.5 GiB with `-K 10000000`, like 0.3.0, and at
15.1 GiB with `-K 100000000` (14.9 GiB for 0.3.0). With the default `-K`
and 9 threads, the CPU backend peaked at 18.6 GiB. From 0.5.0 the CPU
backend does not load the `.0123` file either: it peaked at 10.9 GiB with
`-K 10000000`, like `--metal`. It adds a 45 MB table of the index intervals
of every string of up to 10 bases, built once, for SMEM collection.
From 0.4.1, `mem` warns on stderr when the Mac has less physical memory
than its estimate of the run's peak: the index files it loads (17.0 GB for
hs38 before 0.5.0 on the CPU, 10.8 GB with `--metal` and from 0.5.0 on the CPU), plus 1 GiB, plus 24 bytes (CPU) or
45 bytes (`--metal`) per base of a chunk (`-K`, or 10M bases per thread by
default). That fits the peaks above. The run then still goes ahead with the
same `-K` and output, but expect heavy paging. For hs38 a 16 GB Mac got the
warning on the CPU before 0.5.0, but not with `--metal` (or the 0.5.0 CPU
backend) and up to 8 threads.

`wgse-bwa-mem3` reads the same index as `wgse-bwa-mem2`, without the 6.2 GB
`.0123` file, and like Bioconda's bwa-mem3 0.16.0 peaked at about 10.9 GiB on
hs38 with 9 threads and `-K 10000000`; `--metal` adds about 0.2 GiB, and
0.5 GiB from 0.3.0 (11.2 GiB), whose GPU seeding reads the 5.8 GiB occurrence
table in place: on a GPU whose largest buffer is smaller it seeds on the CPU. Its
denser suffix-array index (`bwa-mem3 re-sa -u 2`) peaked at 14.5 GiB and was
slower on this Mac. From 0.2.0, `mem` warns on stderr, as `wgse-bwa-mem2`
0.4.1 does, when the Mac has less physical memory than its estimate of the
run's peak: the `.bwt.2bit.64` and `.pac` files (10.1 GiB for hs38), plus
1 GiB, plus 24 bytes per base of a chunk (`-K`, or 10M bases per thread by
default). The run still goes ahead with the same output. `wgse-bwa-mem3
capabilities --json` reports `physical_memory_bytes` and the GPU's limits
(`metal.max_buffer_bytes`, `metal.recommended_max_working_set_bytes`).

`wgse-minimap2` uses within about 1 GiB of upstream minimap2's memory: with 9 threads on
hs38 it peaked at 13–15 GiB for HiFi reads, 10–14 GiB for ONT reads and
11.3 GiB for short reads, whether it loaded a `.mmi` index or built one from
the FASTA reference. Since 0.3.0 each mapping thread keeps scratch for sorting
anchors out of place: 21 bytes per anchor of the largest array it sorted, at
most 88 MiB (arrays of more than 4M anchors are sorted in place), so at most
about 0.8 GiB with 9 threads; in the full HiFi and ONT runs peak RSS rose by
at most 0.3 GiB. `--metal` allocated 370–560 MiB of GPU buffers in the full
HiFi and ONT runs below. Its two batch buffer sets can grow to about 0.9 GiB in
all: each holds backtrack matrices of up to 256 MiB, plus sequences and CIGARs,
and grows half again beyond its largest batch. With 9 threads,
its peak resident memory stayed in those ranges. Its peak footprint (resident
plus compressed memory) was about 0.5 GB above the CPU build's for the full HiFi
set (16.7–17.0 GB), and 1.3–2.1 GB above it for ONT (13.9–14.7 GB), whose
reads hold more memory while their gap fills wait for the GPU.

On a base M1 Mac mini with 16 GB, `wgse-bwa` 0.5.0 built the hs38 index with a
6.2 GiB peak. Aligning with 0.7.0 and 8 threads peaked at 5.5 GiB on the CPU and
6.2 GiB of resident memory with `--metal`. Both finished at the same speed
alongside a 6 GB process that pushed about 3.7 GB into swap.

## Performance

Measured on an M1 Pro (8P+2E cores, 32 GB). All runs in each group produced
identical alignments.

Full genome (hs38, first 5M HG002 30x NovaSeq pairs, `-K 10000000`). Each row
is the mean of its runs. The 0.3.0 and 0.2.0 `--metal` rows come from one
session on AC power that interleaved them with the 0.3.0 CPU (two runs each,
three for 0.3.0 `--metal`):

| Aligner | Threads | Time | Peak RSS | vs CPU |
| --- | --- | --- | --- | --- |
| Bioconda bwa 0.7.19 | 9 | 528.5 s | 5.4 GiB | — |
| `wgse-bwa` 0.1.0 | 9 | 527.4 s | 5.4 GiB | — |
| `wgse-bwa` 0.3.0 | 9 | 482.7 s | 5.4 GiB | — |
| `wgse-bwa --metal` 0.2.0 (seeding) | 9 | 423.4 s | 5.8 GiB | −12.3% |
| `wgse-bwa --metal` 0.3.0 (seeding and locate) | 9 | 297.0 s | 5.8 GiB | −38.5% |
| `wgse-bwa --metal` 0.4.0 (also mate rescue) | 9 | 241.2 s | 5.9 GiB | −50.0% |
| `wgse-bwa --metal` 0.5.0 (also seed extension) | 9 | 213.1 s | 6.1 GiB | −55.9% |
| `wgse-bwa --metal` 0.6.0 (faster kernels, overlap) | 9 | 147.1 s | 6.2 GiB | −69.5% |
| `wgse-bwa --metal` 0.7.0 (faster CPU share) | 9 | 136.0 s | 6.2 GiB | −71.8% |

The 0.4.0 row comes from a later session on AC power that interleaved three
runs each of 0.4.0 `--metal` (243.6, 243.0 and 237.0 s), 0.3.0 `--metal`
(314.3, 304.5 and 310.3 s; mean 309.7 s) and the 0.4.0 CPU backend: 0.4.0
`--metal` took 22.1% less time than 0.3.0 `--metal`. Other processes loaded
the machine during two of the CPU runs (579.5 and 564.5 s); the third took
508.2 s. The 0.4.0 row's "vs CPU" therefore uses the earlier session's
482.7 s, in which 0.3.0 `--metal` was 4% faster than in this one.

The 0.5.0 row comes from a third session on AC power that interleaved three
runs each of 0.5.0 `--metal` (209.6, 214.3 and 215.5 s) and 0.4.0 `--metal`
(248.4, 233.4 and 232.5 s; mean 238.1 s), with the order reversed in the
third round, and two of the 0.5.0 CPU backend (548.1 s under background load
and 480.5 s): 0.5.0 `--metal` took 10.5% less time than 0.4.0 `--metal`, and
was faster in each round. It moves most of the gain into mapping (about
115 s, down from about 140 s); the pairing step is unchanged. Its "vs CPU"
uses 482.7 s like the rows above.

The 0.6.0 row comes from a fourth session on AC power that interleaved two
runs each of 0.6.0 `--metal` (146.8 and 147.3 s), 0.5.0 `--metal` (215.6 and
208.9 s; mean 212.3 s) and the 0.6.0 CPU backend (477.7 and 472.0 s), with
light background load (Spotlight and Photos indexing): 0.6.0 `--metal` took
30.7% less time than 0.5.0 `--metal` and 69.0% less than the CPU backend of
the same session. These 10M reads end before the denser suffix-array sample
is built; in runs that build it at the start, it saved a further 10–12% of
the time after the build.

The 0.7.0 row comes from a fifth session on AC power that interleaved three
runs each of 0.7.0 `--metal` (134.1, 133.6 and 140.3 s) and 0.6.0 `--metal`
(146.9, 148.2 and 153.8 s; mean 149.7 s), with the order reversed in the
third round, and two of the 0.7.0 CPU backend (533.6 and 537.9 s, slower than
in earlier sessions; the battery was charging): 0.7.0 `--metal` took 9.1% less
time than 0.6.0 `--metal`, and was faster in each round. Its CPU threads
mapped 2.4M of the 10M reads (1.6M in 0.6.0) and rescued the mates of 2.4M
pairs (1.7M). Its "vs CPU" uses 482.7 s like the rows above.

An earlier session measured 481.2 s (CPU) and 425.4 s (0.2.0 `--metal`). On
200k of those pairs 0.3.0 `--metal` saved 33% with 9 threads, 41% with 4 and
50% with 1, and 0.4.0 `--metal` took a further 21% less time than 0.3.0
`--metal` with 9 threads, and 0.5.0 `--metal` 11% less than 0.4.0 (11.1 s
against 12.4 s, three runs each).

BWA-MEM2's human index needs about 73 GB of memory to build, so it was built on
a Linux machine with upstream bwa-mem2 2.3 (the index files are the same on
macOS). On the same full-genome data and threads, `wgse-bwa-mem2` measured as
follows, in three interleaved rounds on AC power with the order reversed in
the third:

| Aligner | Threads | Time | Peak RSS | vs CPU |
| --- | --- | --- | --- | --- |
| `wgse-bwa-mem2` 0.1.0 | 9 | 447.5 s | 16.3 GiB | — |
| `wgse-bwa-mem2` 0.2.0 | 9 | 458.3 s | 16.3 GiB | — |
| `wgse-bwa-mem2 --metal` 0.1.0 (seed extension) | 9 | 395.7 s | 16.3 GiB | −11.6% |
| `wgse-bwa-mem2 --metal` 0.2.0 (also seeding, rescue, overlap) | 9 | 161.2 s | 16.9 GiB | −64.8% |

0.2.0 `--metal` (167.8, 172.2 and 143.5 s) took 59.3% less time than 0.1.0
`--metal` (440.8, 395.6 and 350.6 s) and was faster in each round; without
overlapping mapping and pairing it took 175.9 s. Each "vs CPU" compares with
the same version's CPU backend. The two CPU builds used the same user time
(2822–2848 s); their wall times varied with system time (144–367 s), which grew
when other applications' memory had to be compressed to make room for the
17 GB process on this 32 GB Mac. A profile of 0.2.0 `--metal` found mapping on
the critical path (162 s), with pairing overlapped under it; the GPU kernels
ran 45 s of seeding, 69 s of mate rescue and 31 s of seed extension.

A later session measured 0.3.0 the same way, in three interleaved rounds on AC
power (the 0.3.0 CPU backend in the first two), with 0.2.0 `--metal` running
first in the second and third:

| Aligner | Threads | Time | Peak RSS | vs CPU |
| --- | --- | --- | --- | --- |
| `wgse-bwa-mem2` 0.3.0 | 9 | 454.2 s | 16.3 GiB | — |
| `wgse-bwa-mem2 --metal` 0.2.0 | 9 | 153.2 s | 16.9 GiB | −66.3% |
| `wgse-bwa-mem2 --metal` 0.3.0 | 9 | 116.6 s | 11.5 GiB | −74.3% |

0.3.0 `--metal` (117.3, 116.5 and 116.0 s) took 23.9% less time than 0.2.0
`--metal` (150.1, 155.8 and 153.8 s) and was faster in each round; in an
earlier session with less memory pressure, 0.2.0 `--metal` took 138.9–141.4 s
and 0.3.0 `--metal` 116.3–118.4 s (17% less). 0.2.0 `--metal` spent 68–90 s in
the kernel (5.9–8.5M page reclaims) making room for its 17 GB; 0.3.0 spent
12 s. The CPU backend is unchanged in 0.3.0. With the default `-K`, 0.3.0
`--metal` took 115.5 s and 0.2.0 `--metal` 160.8 s (one run each). A profile
of 0.3.0 `--metal` found the GPU busy 88% of the run (extension 46 s, seeding
38 s, mate rescue 37 s), with CPU threads seeding 16 of 4983 units; their
busy time went to seed extension the GPU had no room for (21%), mate rescue
(17%) and suffix-array locate (14%).

0.4.0 was measured against 0.3.0 the same way, in three interleaved rounds on
AC power, with `-K 10000000` and with `-K 100000000` (the batch size WGS
Extract passes):

| Aligner | `-K` | Time | Peak RSS | vs 0.3.0 |
| --- | --- | --- | --- | --- |
| `wgse-bwa-mem2 --metal` 0.3.0 | 10M | 116.6 s | 11.5 GiB | — |
| `wgse-bwa-mem2 --metal` 0.4.0 | 10M | 110.2 s | 11.5 GiB | −5.5% |
| `wgse-bwa-mem2 --metal` 0.3.0 | 100M | 114.6 s | 14.9 GiB | — |
| `wgse-bwa-mem2 --metal` 0.4.0 | 100M | 108.9 s | 15.1 GiB | −4.9% |

0.4.0 `--metal` (110.8, 109.9 and 110.0 s; 110.4, 108.3 and 108.1 s) was
faster than 0.3.0 `--metal` (117.6, 115.9 and 116.3 s; 116.1, 114.1 and
113.5 s) in each round. Most of the gain comes from the cheaper extension and
rescue kernels; in another session the prefix table and 8-bit extensions on
the CPU saved a further 2.3% (110.3 s against 112.9 s, faster in each round).
The CPU backend is unchanged in 0.4.0. A profile of 0.3.0 `--metal` found the
GPU the bottleneck: mate rescue is compute-bound, and seeding is bound by
address translation on the 5.8 GiB occurrence table. With 8-bit extensions on
the CPU threads, the GPU extended 73M pairs rather than 84M, leaving it more
time for seeding and rescue. Whether the GPU or the CPU threads should run
8-bit extensions depends on the Mac; with a larger GPU, keeping them on the
GPU may be faster.

On the same data, `wgse-bwa --metal` 0.7.1, which shares the updated
mate-rescue kernel, took 125.9 and 125.5 s against 130.5 and 129.2 s for
0.7.0 in two interleaved rounds, with identical alignments.

`wgse-bwa-mem2` 0.5.0's CPU backend uses the exact replacements described
under Usage. On the same data, with 9 threads and `-K 10000000`, three
interleaved rounds on AC power took 219.0 s (219.0, 220.6 and 214.3 s) against
519.0 s for 0.4.1 (394.9, 874.3 and 519.0 s), with 47% less CPU time (1731
against 3248 s) and a 10.9 GiB peak against 16.3 GiB. Other agents kept the
load average between 11 and 66 and the swap in use; 0.4.1, needing 6 GiB more,
paged the most, so its fastest round (394.9 s, like the 394.2 s of an earlier
run under less load) is the fairer comparison: 0.5.0 took 44% less time. Without the SMEM changes 0.5.0 took 276.6 s.
`--metal` is unchanged (122.1 s against 119.9 s for 0.4.1, within noise).

Before the full index was available, BWA-MEM2 was measured with 10.6M read
pairs against a small reference (chr20 fragment, Y, MT) whose whole index fits
in cache:

| Aligner | Threads | Time | vs packaged build |
| --- | --- | --- | --- |
| Bioconda bwa 0.7.19 | 9 | 53.2 s | — |
| `wgse-bwa` | 9 | 51.9 s | −2.5% |
| Bioconda bwa-mem2 2.3 | 8 | 45.7 s | — |
| `wgse-bwa-mem2` | 9 | 35.0 s | −23.5% |
| `wgse-bwa-mem2 --metal` | 9 | 31.0 s | −32.2% |

On a base M1 Mac mini (4P+4E cores, 8-core GPU, 16 GB) with the same 5M pairs
and `-K 10000000`, with a display attached. Each row's runs produced identical
alignments and exited 0, and no command buffer was aborted. `wgse-bwa` used
hs38; `wgse-bwa-mem2` used the small reference, as its full index does not fit
in this memory on the CPU and was not built for `--metal`:

| Aligner | Threads | Time | Peak RSS | vs CPU |
| --- | --- | --- | --- | --- |
| `wgse-bwa` 0.7.0 | 4 | 932.8 s | 5.4 GiB | — |
| `wgse-bwa --metal` 0.7.0 | 4 | 214.8 s | 6.2 GiB | −77.0% |
| `wgse-bwa` 0.7.0 | 8 | 611.8 s | 5.5 GiB | — |
| `wgse-bwa --metal` 0.7.0 | 8 | 211.6 s | 6.2 GiB | −65.4% |
| `wgse-bwa-mem2` 0.3.0 | 4 | 1589.2 s | 1.3 GiB | — |
| `wgse-bwa-mem2 --metal` 0.3.0 | 4 | 383.4 s | 2.1 GiB | −75.9% |
| `wgse-bwa-mem2` 0.3.0 | 8 | 985.3 s | 1.4 GiB | — |
| `wgse-bwa-mem2 --metal` 0.3.0 | 8 | 373.8 s | 2.4 GiB | −62.1% |

The `--metal` 8-thread `wgse-bwa` row is the mean of three runs. With an animated
browser page redrawing the display, `wgse-bwa --metal` took 280.0 s with 8
threads (four runs) and 284.5 s with 4. Alongside a 6 GB process it took 211.5 s
with 8 threads and 215.2 s with 4, against 613.7 s on the CPU with 8. On this
machine `wgse-bwa --metal` 0.5.0 was aborted in about half of its full-genome
runs and then finished on the CPU with exit status 1; 0.7.0 was not aborted in
any of its 11. Building the hs38 BWA index with 0.5.0 took 41 minutes.

On the CPU `wgse-bwa` 0.7.1 matches the packaged build. Compiler tuning does not help:
Apple clang already targets `apple-m1`, and ThinLTO, full LTO, profile-guided
optimization and mimalloc each changed the 200k-pair time by about 1% or less,
and mimalloc changed the full-genome time by 0.4%.

`wgse-bwa` 0.8.0's CPU backend uses the exact replacements described under
Usage. On the same data, with 9 threads and `-K 10000000`, three interleaved
rounds on AC power took 363.0 s (363.0, 367.9 and 349.7 s) against 530.5 s for
0.7.1 (526.4, 547.6 and 530.5 s): 32.8% less, faster in each round, with 29%
less CPU time (2976 against 4189 s) and the same peak RSS (5.5 GiB). Other
agents' builds kept the load average between 7 and 100 during both arms. A
profile of 0.7.1 on the first 200k pairs had put 32% of the CPU time in mate
rescue (28% in `ksw_u8`), 22% in suffix-array locate (`bwt_sa`), 19% in SMEM
collection and 19% in seed extension (`ksw_extend2`).

`wgse-bwa` 0.9.0's CPU backend adds the NEON seed extension and the
interleaved seeding described under Usage. Three interleaved rounds on the same
data (9 threads, `-K 10000000`, AC power, each run started after the machine
had cooled to nominal thermal pressure, which then held throughout) took
246.4 s (246.4, 245.1 and 246.9 s) against 339.2 s for 0.8.0 (339.3, 339.2 and
336.5 s): 27.4% less, faster in each round, with 27% less CPU time (2157
against 2949 s). The NEON extension alone took 286.2 s (15.6% less than 0.8.0).
On one thread, collecting the SMEM intervals of 10,000 reads took 38% less time
with the interleaved walks than with upstream's.

`wgse-bwa` 0.11.0 was measured against 0.9.0 on the same data in interleaved
rounds at nominal thermal pressure, while other work kept the load average at
3 to 10 (so both took longer than above). With `hs38.fa.wgse-sa16` its CPU
backend took 266.4 s against 301.1 s (11.5% less, faster in both rounds, 11%
less CPU time) and `--metal` 125.7 s against 136.1 s (7.7% less, faster in all
three rounds, 7% less CPU time); the file raised the peak RSS by 0.75 GiB (to
6.2 GiB on the CPU and 7.0 GiB with `--metal`). With `--dense-sa off` 0.11.0
took 5.8% less time than 0.9.0 on the CPU and 1.5% less with `--metal`.

On hs38 BWA's time splits roughly into SMEM collection (19%), suffix-array
locate (23%) and extension with mate rescue (45%). `--metal` 0.2.0 moved only
SMEM collection to the GPU, so it could save at most about a fifth of the run;
0.3.0 also locates the seeds there, for about 88% of the pairs. A profile of
0.3.0 `--metal` then put 52% of the CPU threads' busy time in mate rescue
(`ksw_align2`, 45% in its 8-bit kernel) and 30% in seed extension
(`ksw_extend2`). 0.4.0 aligns the rescue windows of about 45% of the pairs on
the GPU, which left seed extension (41% of the CPU threads' busy time) and the
remaining rescue on the CPU threads; 0.5.0 extends the seeds of most pairs on
the GPU. The GPU was then busy for most of both steps (its seeding, locate and
extension work in mapping, and mate rescue in pairing), but mapping was bound
by the CPU threads and pairing by the GPU. 0.6.0 therefore makes the rescue and
extension kernels faster and overlaps the two steps of consecutive chunks, and
borrows two ideas from BWA-MEM2 without its larger index: counting bases in a
whole occurrence block at once, and (on long runs) a denser suffix-array
sample. A profile of 0.6.0 `--metal` found the GPU busy for 94% of the run,
with its seeding and locate kernels bound by memory latency, while the CPU
threads spent 30% of their busy time in mate rescue's 8-bit kernel and 10% in
locate. 0.7.0 therefore speeds up the CPU threads' share, so that they take
more of the work from the GPU.

`wgse-minimap2` 0.1.0 was measured against Bioconda's minimap2 2.31 (the
default install) on hs38 with 9 threads, in three interleaved rounds on AC
power. The "plain" build is `wgse-minimap2` without the changes to upstream's
code listed under Usage: Apple clang `-O3` and the SSE4.1 alignment kernels
alone. Each group's runs wrote identical alignments (SHA-256 of the SAM
without `@PG`, first 16 hex digits):

| Reads | Index | Bioconda minimap2 | Plain build | `wgse-minimap2` 0.1.0 | Peak RSS (Bioconda / 0.1.0) | Alignments |
| --- | --- | --- | --- | --- | --- | --- |
| HiFi, 145k reads, 2.5 Gbp (`map-hifi`) | `.mmi` | 98.8 s | 89.8 s | 74.1 s (−25.0%) | 13.9 / 14.9 GiB | `bf2671dae7d3260e` |
| ONT, 45.6k reads, 0.88 Gbp (`map-ont`) | `.mmi` | 141.1 s | 131.3 s | 124.9 s (−11.5%) | 11.4 / 11.0 GiB | `3a4c91aa2339b70f` |
| 1M 150 bp pairs (`sr`) | `.mmi` | 42.5 s | 42.2 s | 25.7 s (−39.5%) | 11.6 / 11.3 GiB | `5f521317c321d233` |
| HiFi, 40k reads, 0.69 Gbp (`map-hifi`) | FASTA | 55.3 s | — | 31.2 s (−43.6%) | 14.3 / 14.6 GiB | `3b9686f30aff954f` |
| ONT, 15k reads, 0.29 Gbp (`map-ont`) | FASTA | 66.2 s | — | 37.2 s (−43.8%) | 14.0 / 13.2 GiB | `0d2d6def67484e12` |

The HiFi reads are the start of PacBio's public Revio HG002 run
(`m84011_220902_175841_s1`); the ONT reads are the HG002 reads (Dorado hac,
R10.4.1, GIAB 2025.01) that overlap a 750 kb window of each of chr1–22 and X;
the short reads are the first 1M of the HG002 pairs above. With 4 threads the
ONT alignments were the same as with 9.

Building the index from the FASTA reference, as `wgsextract align` does, is
where 0.1.0 saves most: upstream sketches each chromosome on one thread and
selects `mid_occ` serially, so its `map-hifi` index was ready after 31.0 s,
against 7.4 s for 0.1.0. Loading a `.mmi` and computing its statistics took
14.7 s for `map-hifi`, 5.9 s for `map-ont` and 15.6 s for `sr` upstream,
against 2.3, 1.5 and 2.5 s. In mapping, skipping radix-sort passes saved 10% of the CPU
time on the ONT subset and 4% on the HiFi subset, and prefetching seeds 2% on
ONT; the plain build's compiler and kernels saved 7–9% of the CPU time
against Bioconda's build. Scoring chaining predecessors eight at a time with
NEON gave nothing, because most predecessor scans stop early, and was dropped.
A profile of the plain build's mapping found base-level alignment
(`ksw_extd2`, about 42% of busy time) dominating HiFi reads, anchor sorting
and chaining (about 70%) dominating ONT reads, and minimizer lookups and seed
collection (about 58%) dominating short reads. 0.1.0 leaves that mostly
unchanged, so those are the targets for GPU work.

`wgse-minimap2` 0.2.0 `--metal` was measured in one session on the same
data, with 9 threads, `.mmi` indexes and AC power. The 0.1.0 and `--metal`
runs were interleaved, three rounds each (the Bioconda baseline ran once).
Other agents' work kept the machine busier than in the 0.1.0 table, so
compare within a row:

| Reads | Bioconda minimap2 | `wgse-minimap2` 0.1.0 | 0.2.0 `--metal` | Peak RSS (Bioconda / 0.1.0 / `--metal`) | Alignments |
| --- | --- | --- | --- | --- | --- |
| HiFi, 145k reads, 2.5 Gbp (`map-hifi`) | 116.6 s | 89.7 s | 71.5 s (−20.3%) | 12.0 / 12.0–12.2 / 12.2–12.5 GiB | `bf2671dae7d3260e` |
| ONT, 45.6k reads, 0.88 Gbp (`map-ont`) | 146.6 s | 121.8 s | 111.7 s (−8.3%) | 10.6 / 10.6–12.0 / 11.1–11.6 GiB | `3a4c91aa2339b70f` |
| 1M 150 bp pairs (`sr`) | 42.7 s | 25.5 s | 25.6 s (CPU) | 11.7 / 11.3 / 11.3 GiB | `5f521317c321d233` |

`--metal` moves the gap fills between anchors (`ksw_extd2`'s approximate
mode, 12.7M fills for the HiFi set) to the GPU. The GPU computed 85% of the
HiFi fills, at about 6.9 G cells/s, and CPU threads with no read left to map
computed the rest. That cut the HiFi CPU time from about 645 to 450 s. Fills
whose cost exceeds a cap stay on the CPU: about 3% of HiFi fills, but the
longest. The GPU computed nearly all of ONT's 4.4M fills, but ONT reads spend
most of their time on anchor sorting and chaining, so the gain is smaller.
The `sr` preset stays on the CPU under `--metal`.

A profile of `--metal` mapping (busy time, top of stack) found:

- HiFi: the CPU's remaining `ksw_extd2` fills at 25%, anchor sorting
  (`rs_sort_128x`) at 16%, and chaining (DP plus RMQ) at about 30%.
- ONT: anchor sorting at 40%, chaining at about 38%, and `ksw_extd2` at 4%.
  Sorting the seed hits in `collect_seed_hits` alone took a third of ONT's
  busy time.

Two approaches gained nothing: a GPU chaining kernel, and parking an OS
thread on each read while its fills waited for the GPU. What paid off was:

- batches of about 1,300 fills of similar size;
- many reads in flight per OS thread (16 fibers sharing one memory arena);
- CPU threads taking queued fills when they ran out of reads.

`wgse-minimap2` 0.3.0 was measured against 0.2.0 in three or four interleaved rounds
on the same data, with 9 threads, `.mmi` indexes and AC power, each run
started at thermal pressure 0 and a 1-minute load average below 2 (some
below 3.5, when the shared machine's idle load stayed above 2). Medians:

| Reads | 0.2.0 | 0.3.0 | 0.2.0 `--metal` | 0.3.0 `--metal` | Alignments |
| --- | --- | --- | --- | --- | --- |
| HiFi, 145k reads, 2.5 Gbp (`map-hifi`) | 77.8 s | 71.1 s (−8.7%) | 62.8 s | 50.6 s (−19.3%) | `bf2671dae7d3260e` |
| ONT, 45.6k reads, 0.88 Gbp (`map-ont`) | 117.5 s | 106.6 s (−9.3%) | 109.0 s | 97.1 s (−10.9%) | `3a4c91aa2339b70f` |
| 1M 150 bp pairs (`sr`) | 29.1 s | 26.8 s (−7.9%) | — | — | `5f521317c321d233` |

Three changes, each exact:

- The anchor sort replays upstream's in-place radix sort out of place: one
  in-place pass of the American-flag sort is a walk that takes the next
  element of the current bucket's region, delivers it to its digit's bucket
  and continues in that bucket. Replaying that walk on the digits alone and
  then gathering the elements gives upstream's order exactly, including that
  of equal keys, which chaining depends on (most ONT reads have anchors with
  equal keys), so a stable sort would change the output. It cut the sort's
  cycles by about 28%.
- `ksw_extd2` is written for NEON (`EXT` for the shifted column, bit-select
  for the backtrack flags) instead of going through sse2neon: about 9% faster
  on HiFi fills.
- The Metal gap-fill kernels compute in 16 bits instead of 32 when the
  scoring keeps every cell in range (checked from the scoring and each fill),
  with lane widths matched to the fill sizes: about 1.8 times the cells per
  second.

Rejected for 0.3.0, as each gained less than 5%: scoring chaining
predecessors four at a time with NEON (exact, but slower: the scalar loop is
bound by instructions, not branches).

`wgse-bwa-mem3` 0.1.0 was measured against Bioconda's bwa-mem3 0.16.0 on hs38
(the first 5M HG002 pairs above, 9 threads, `-K 10000000`) in interleaved
rounds on AC power, with the machine shared with other work (load averages
10–40). Each mode's runs wrote identical alignments (SHA-256 of the SAM
without `@PG`, first 16 hex digits). Times are means of the clean runs; runs
that overlapped other benchmarks were discarded:

| Mode | Bioconda bwa-mem3 0.16.0 | `wgse-bwa-mem3` 0.1.0 | Peak RSS (Bioconda / 0.1.0) | Alignments |
| --- | --- | --- | --- | --- |
| plain (bwa-mem3's records) | 73.7 s (1 run) | 75.6 s (3 runs: 82.1, 71.3, 73.3) | 10.9 / 10.7 GiB | `895c361c65ceb365` |
| `--compat=bwa-mem` | 74.3 s (76.8, 71.8) | 74.7 s (75.0, 74.4) | 10.9 / 10.7 GiB | `e2febf171215d840` |
| `--compat=bwa-mem2` | 71.8 s (71.0, 72.5) | 71.4 s (69.6, 73.1) | 10.9 / 10.7 GiB | `dff39743a72242de` |
| `--fast` | 34.7 s (32.8, 37.0, 34.2) | 35.0 s (33.7, 35.2, 36.1) | 10.7 / 10.5 GiB | `d27f9ccd7a921c97` |

The `--compat` hashes are those of `wgse-bwa` and `wgse-bwa-mem2`. bwa-mem3
already has NEON kernels for Apple Silicon, so 0.1.0 is upstream's code built
with Apple clang, and as fast as Bioconda's build. Upstream's stage timer
(wall seconds, plain mode, 73.3 s run) split the time into reading reads
8.2 s, SMEM seeding 12.3 s, chaining 8.6 s, suffix-array lookup 8.5 s, banded
Smith-Waterman seed extension (BSW) 23.5 s, and pairing, mate rescue and SAM
formatting 23.9 s; `--fast` cut BSW to 5.2 s and the pairing step to 6.3 s.
None of these changes was faster, with identical output, so 0.1.0 has none of
them:

- making the bundled mimalloc replace `malloc` (Bioconda's build notes that it
  does not): 75.8 s plain and 33.5 s `--fast` over three rounds;
- that plus link-time optimisation: 78.1 s plain;
- profile-guided optimisation: no faster on chr1–3 in any mode;
- `-mcpu=apple-m1`: the same code;
- the denser suffix-array sample (`re-sa -u 2`): slower in every mode tried
  (81–94 s, and 42 s with `--fast`), at 14.5 GiB.

`wgse-bwa-mem3` 0.2.0 `--metal` was measured the same way, against its own
CPU backend (0.1.0) and Bioconda's build, in interleaved rounds on a later
day (load averages 5–47); the last round ran 0.2.0 as released, with its
default settings. Every run of a mode wrote the alignments above. Times are
means, with each run in brackets:

| Mode | Bioconda bwa-mem3 0.16.0 | CPU, `-t 9` | `--metal`, `-t 9` | Change | Peak RSS (CPU / `--metal`) |
| --- | --- | --- | --- | --- | --- |
| plain | 69.0 s | 70.8 s (71.8, 78.7, 69.4, 68.5, 68.4, 69.1, 69.8) | 64.0 s (63.3, 63.9, 65.0, 63.0, 63.0, 62.9, 66.8) | −9.6% | 10.7 / 10.9 GiB |
| `--compat=bwa-mem` | 68.3 s | 68.5 s (69.0, 67.5, 67.6, 69.9) | 63.9 s (63.0, 65.4, 62.5, 64.7) | −6.7% | 10.7 / 10.9 GiB |
| `--compat=bwa-mem2` | 70.1 s | 69.5 s (69.4, 68.7, 68.7, 71.3) | 63.6 s (65.1, 63.2, 62.9, 63.2) | −8.5% | 10.7 / 10.9 GiB |
| `--fast` | 31.8 s | 32.5 s (31.7, 34.9, 31.0, 32.3) | 33.2 s (31.4, 36.3, 31.9, 33.3) | +2.3% | 10.5 / 10.6 GiB |

The CPU threads wait for the GPU, so `--metal` runs 18 alignment threads for
`-t 9`. More CPU threads alone do not explain the gain: in the same rounds,
plain mode on the CPU took 68.4 s with `-t 10` (68.5, 69.6, 67.0) and 67.9 s
with `-t 18` (69.9, 67.0, 66.7). The GPU scored all of a run's 8-bit
extension batches: 63.1M extensions in about 7,500 command buffers and 41 s
of GPU time in plain and `--compat` modes, and 13.5M in 15–16 s with
`--fast`, whose extensions are too few for the GPU to save time. The GPU
extends seeds more slowly than nine CPU threads (upstream spends 23.5 s of
wall time on it), but frees those threads for the other stages. On
the `medium` bench tier (500k pairs on chr1–3), where loading the index
takes much of the time, the gain was within the noise. Rescuing mates on the
GPU with `wgse-bwa`'s kernel was also tried; it was no faster on `medium`, so
0.2.0 does not do it.

`wgse-bwa-mem3` 0.3.0 `--metal` (GPU seeding) was measured against 0.2.0
`--metal` on the same 5M pairs, `-t 9`, in interleaved rounds on AC power,
each run started at thermal pressure 0 (load averages 1.8–5.7 from other
work). Every run of a mode wrote the alignments above:

| Mode | 0.2.0 `--metal` | 0.3.0 `--metal` | Change | CPU, `-t 9` | Peak RSS (0.2.0 / 0.3.0 `--metal`) |
| --- | --- | --- | --- | --- | --- |
| plain | 63.2 s (65.1, 62.5, 62.1) | 57.6 s (56.7, 60.4, 55.8) | −8.9% | 68.0 s (68.9, 67.1) | 10.9 / 11.2 GiB |
| `--compat=bwa-mem` | 65.1 s | 61.9 s | −5.0% | | 10.9 / 11.2 GiB |
| `--compat=bwa-mem2` | 67.0 s | 59.1 s | −11.7% | | 10.9 / 11.2 GiB |

The GPU seeded 9.92M of the 9.93M reads (the CPU the rest) in 42–45 s of GPU
time, so with seed extension the GPU is now busy for most of the run, and
the CPU threads, which spent about 18% of their time seeding, score
extensions when the GPU falls behind. Each change alone gained little: GPU
seeding with every extension on the GPU took 63.4 s (64.6, 63.0, 62.6), and
the CPU taking extensions without GPU seeding 63.2 s (64.0, 62.4).
`--fast` gains nothing measurable: in one interleaved round (load averages
2–3.3), 0.3.0 `--metal` took 42.9 s, 0.2.0 `--metal` 44.2 s, the CPU 43.8 s
and 0.3.0 with `--gpu-seed off` 45.3 s, all writing `d27f9ccd7a921c97`.
Also tried, and not faster: four or eight command buffers in flight for seed
extension (the buffers slowed each other), and one GPU thread per extension
with interleaved bases (wgse-bwa-mem2's layout; the same time).

### Energy

Each aligner's energy was measured with `bench/bench.sh --power` on the same
M1 Pro, on the 5M hs38 pairs with 9 threads (`-K 10000000` for the BWA tools,
minimap2's defaults with `-x sr` and its `.mmi`). The machine was on AC power
with the battery charged, and no other benchmark or build was running. The
tools ran in one session of two rounds, the second in reverse order, and every
run wrote the expected alignments. Each row is the mean of two runs:

| Aligner | Time | Mean power | CPU Wh | GPU Wh | Package Wh | Above idle Wh | Package Wh per 30x |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Bioconda bwa 0.7.19 | 470.5 s | 21.2 W | 2.76 | 0.00 | 2.77 | 2.55 | 172 |
| `wgse-bwa` 0.7.1 | 479.6 s | 20.8 W | 2.77 | 0.00 | 2.77 | 2.62 | 172 |
| `wgse-bwa --metal` 0.7.1 | 127.9 s | 38.4 W | 0.97 | 0.39 | 1.37 | 1.33 | 85 |
| Bioconda bwa-mem2 2.3 | 381.1 s | 24.0 W | 2.52 | 0.00 | 2.53 | 2.43 | 157 |
| `wgse-bwa-mem2` 0.4.0 | 459.8 s | 21.1 W | 2.61 | 0.00 | 2.64 | 2.53 | 164 |
| `wgse-bwa-mem2 --metal` 0.4.0 | 117.7 s | 39.7 W | 0.98 | 0.32 | 1.30 | 1.27 | 80 |
| Bioconda bwa-mem3 0.16.0 | 70.4 s | 31.4 W | 0.61 | 0.00 | 0.61 | 0.59 | 38 |
| Bioconda minimap2 2.31 (`sr`) | 150.2 s | 26.8 W | 1.10 | 0.00 | 1.12 | 1.05 | 69 |
| `wgse-minimap2` 0.1.0 (`sr`) | 118.8 s | 30.1 W | 0.98 | 0.00 | 0.99 | 0.93 | 62 |

`--metal` roughly halves the energy of both BWA tools, less than it cuts their
time (by about 73%): the GPU adds 0.3–0.4 Wh, and alongside it the CPU threads
draw more power (27–30 W against 21 W) than when they align on their own.
The two `wgse-bwa-mem2` CPU runs took 553.1 and 366.5 s (2.91 and 2.36 Wh):
the first spent 610 s in the kernel, against 48 s, while other applications'
memory was compressed and swapped to make room for its 16 GB. The Bioconda
bwa-mem2 runs (415.4 and 346.8 s) varied the same way, less.

Method: `bench/power.py` runs `powermetrics --samplers cpu_power,gpu_power -i
1000 -f plist` for the whole run and sums the CPU, GPU and Neural Engine energy
counters of each 1 s sample (prorated at the ends of the run). "Package" is
their sum, which leaves out memory, SSD, display and the rest of the
machine, so energy at the wall is higher. Before each run the sampler records
60 s of idle; "above idle" subtracts that mean power over the run's time,
leaving the aligner's own energy apart from background work (Photos and
Spotlight indexing at times; the idle baseline ranged from 0.6 to 3.1 W). The
Neural Engine counter only saw that background work (at most 0.03 Wh per run).
"Per 30x" scales the package energy by 62: a 30x human genome is about 93 Gbp,
and these 5M pairs of 150 bp reads are 1.5 Gbp. That slightly overstates it,
since a whole genome loads the index once rather than 62 times. Each run's
directory in the benchmark's output keeps its samples, idle baseline and load
average (`power.txt`, `power-samples.tsv`).

## Linux x86-64 and NVIDIA GPUs (CUDA)

`wgse-bwa` also builds for Linux x86-64 and ARM64, as conda packages
(`packaging/wgse-bwa`, built by `.github/workflows/linux_aligners.yml` and
published as `linux-aligners-*` prereleases; `linux-aligners-2` has 0.12.1, and
`wgse-bwa-mem3` 0.4.0 for x86-64, see
[below](#wgse-bwa-mem3-on-linux-x86-64)).
Download the package for your machine, check it against the SHA-256 digest
GitHub publishes, and install it with conda or mamba into its own environment
together with its runtime libraries (installing a local package file doesn't
resolve dependencies). The CLI finds `wgse-bwa` on `PATH`:

```bash
PKG=wgse-bwa-0.12.1-linux64_wgse_0.conda   # wgse-bwa-0.12.1-linuxaarch64_wgse_0.conda on ARM64
gh release download linux-aligners-2 -R WGSExtract/wgsextract-cli -p "$PKG" --clobber
gh release view linux-aligners-2 -R WGSExtract/wgsextract-cli --json assets \
  --jq '.assets[] | "\(.digest | ltrimstr("sha256:"))  \(.name)"' |
  grep -F "  $PKG" | sha256sum -c &&
conda create -y -n wgse-bwa -c conda-forge "libgcc>=14" "libstdcxx>=14" "libzlib>=1.3.2,<2" &&
conda install -y -n wgse-bwa "./$PKG" &&
export PATH="$(conda run -n wgse-bwa printenv CONDA_PREFIX)/bin:$PATH"
```

- **CPU backend:** upstream BWA's code. The NEON replacements are Apple-only,
  so on x86-64 it runs upstream's `ksw_extend2`; it keeps the other exact
  batched steps.
- **`--cuda` (`--backend cuda`, experimental):** runs the Metal backend's
  kernels on an NVIDIA GPU. The kernels are ported to CUDA from one source
  that HIP can also compile (`native/gpu-common`, `native/bwa/*.cuh`). They
  cover SMEM seeding, suffix-array locate, mate rescue and seed extension, with
  the same scheduling and the same exact-input matching as `--metal`.
- **`--backend auto`:** uses CUDA in this build, and falls back to the CPU as
  on macOS.

Unlike Apple's unified memory, a discrete GPU has its own:

- **Index:** the BWT and the sampled suffix array (about 4.7 GiB for hs38) are
  uploaded once per run.
- **Batches:** each batch's reads, rescue windows and extension jobs cross
  PCIe through pinned buffers. Only compact results come back.
- **GPU memory:** before each upload `--cuda` checks the GPU's free memory,
  keeping 1 GiB spare, because the GPU may be shared. If the BWT doesn't fit,
  the run aligns on the CPU, as after a GPU failure. If only the suffix array
  doesn't fit, the CPU locates seeds and the GPU does the rest.
- **Host memory:** the pinned batch buffers add about 0.7 GiB.
- **Runtime:** the CUDA runtime is linked statically, so the package needs only
  the NVIDIA driver, and only for `--cuda`. Without a usable driver or GPU,
  `capabilities --json` reports `cuda_available: false`.
- **Watchdog:** every wait for the GPU is bounded, by the larger of 60 s and 20
  times that stage's slowest batch so far. A batch that overruns (a hung GPU)
  is finished on the CPU with the same output. `--cuda` then exits 1, and
  `--backend auto` exits 0.

On an RTX 3090 with a Ryzen 5 5600X, aligning the full-5m tier (5M HG002 pairs
to hs38) at 8 threads (`bench.sh` with `bwa:cpu,bwa:cuda`, two interleaved
rounds, medians), with records identical to upstream BWA (the `expected.tsv`
hash):

| | Wall | CPU time | Peak RSS |
| --- | ---: | ---: | ---: |
| upstream BWA 0.7.19 (Bioconda) | 472.5 s | 3711 s | 5.2 GiB |
| `wgse-bwa` (CPU) | 418.9 s (−11%) | 3224 s | 5.2 GiB |
| `wgse-bwa --cuda` | 131.4 s (−72%) | 1006 s | 6.3 GiB |

`--cuda` used at most 5.2 GiB of GPU memory. On the CPU, `perf` puts a quarter
of the time in SMEM seeding, a quarter in seed extension, a fifth in mate rescue
and an eighth in suffix-array locate. With `--cuda` the CPU does the chaining,
the sorts and reference fetches that remain, and spends a quarter as much time.

On the same machine and tier, bwa-mem3 0.16.0 `--compat=bwa-mem` on the CPU
(as `wgse-bwa-mem3`, below) took 97.7 s and 761 s of CPU time, with the same records
as BWA: faster than `wgse-bwa --cuda`. So `align` and `realign` on Linux run
bwa-mem3 in bwa's place first when it's installed and fits, and `wgse-bwa`'s
GPU backend next (#736; [hardware guide](../docs/hardware.md#nvidia-gpus-without-parabricks-wgse-bwa---cuda)).

### wgse-bwa-mem3 on Linux x86-64

`wgse-bwa-mem3` also builds on Linux x86-64, as upstream's default build does:
AVX2 for most code, and the SIMD kernels for each tier from SSE4.1 to AVX-512BW,
picked at run time. It doesn't replace `malloc` with mimalloc, as Bioconda's
build does (#727). Its conda package (`packaging/wgse-bwa-mem3`, with the CUDA
backend) is built by the same workflow as `wgse-bwa`'s and published next to it
in the `linux-aligners-*` prereleases; `packaging/wgse-bwa` leaves it out
(`-DWGSE_BWA_MEM3=OFF`), and it builds without `wgse-bwa` (`-DWGSE_BWA=OFF`).
On `PATH`, `align` and `realign` run it in Bioconda's bwa-mem3's place
([docs/hardware.md](../docs/hardware.md#bwa-mem3-on-linux)), with
`--backend auto`. It needs an x86-64-v3 (AVX2) CPU. Install it as `wgse-bwa`
above, in its own environment:

```bash
PKG=wgse-bwa-mem3-0.4.0-linux64_wgse_0.conda
gh release download linux-aligners-2 -R WGSExtract/wgsextract-cli -p "$PKG" --clobber
gh release view linux-aligners-2 -R WGSExtract/wgsextract-cli --json assets \
  --jq '.assets[] | "\(.digest | ltrimstr("sha256:"))  \(.name)"' |
  grep -F "  $PKG" | sha256sum -c &&
conda create -y -n wgse-bwa-mem3 -c conda-forge "libgcc>=14" "libstdcxx>=14" \
  "libzlib>=1.3.2,<2" "_x86_64-microarch-level>=3" &&
conda install -y -n wgse-bwa-mem3 "./$PKG" &&
export PATH="$(conda run -n wgse-bwa-mem3 printenv CONDA_PREFIX)/bin:$PATH"
```

With `-DWGSE_CUDA=ON`, `--cuda` (`--backend cuda`, experimental) runs three
stages on an NVIDIA GPU:

- **SMEM collection:** `bwa-mem2/seeding.cuh`, ported from `seeding.metal`.
  `seeding_host.cpp` schedules it for both backends. The occurrence table,
  about 6 GiB for hs38, is uploaded once.
- **8-bit seed extension:** `extension.cuh`, ported from `extension.metal`. It is
  hooked into upstream's SSE, AVX2 and AVX-512 `getScores8`, as well as NEON's.
- **8-bit mate rescue (CUDA only):** the pairs of `mem_sam_pe_batch`'s 8-bit
  kswv run `wgse-bwa`'s `ksw_align2` kernel (`bwa/rescue.cuh`), both passes at
  once (`rescue_host.cpp`, `cuda_rescue.cu`). kswv scores the rest, such as
  16-bit windows and pairs whose query is over 256 bases. Each worker thread
  has its own CUDA stream. `--gpu-rescue off` leaves rescue on the CPU.

`--cuda` runs one compute thread per `-t` thread by default. More threads than
cores were slower on x86-64.

The full-5m tier on the same machine (8 threads, two interleaved rounds, means;
the machine was busier than for the `wgse-bwa` table above):

| | Wall | CPU time | Peak RSS | Records |
| --- | ---: | ---: | ---: | --- |
| `wgse-bwa-mem3` | 105.9 s | 817 s | 10.5 GiB | bwa-mem3's |
| `wgse-bwa-mem3 --cuda --gpu-rescue off` | 94.8 s (−10%) | 579 s | 10.9 GiB | bwa-mem3's |
| `wgse-bwa-mem3 --cuda` | 88.1 s (−17%) | 418 s (−49%) | 11.0 GiB | bwa-mem3's |
| `wgse-bwa-mem3 mem --compat=bwa-mem` | 107.5 s | 828 s | 10.5 GiB | BWA's |
| `wgse-bwa-mem3 --cuda mem --compat=bwa-mem` | 87.3 s (−19%) | 425 s (−49%) | 11.0 GiB | BWA's |

The records match `expected.tsv`: bwa-mem3's own, and with `--compat=bwa-mem`,
BWA's. `--cuda` used at most 6.5 GiB of GPU memory. In a quieter session
`wgse-bwa-mem3` took 99.1 s and `--cuda` 84.5 s, and `wgse-bwa --cuda` (BWA's
records) took 130.5 s in another.

On x86-64, `perf` puts a fifth of the CPU backend's time in mate rescue
(`kswv`), more than a quarter in SMEM collection, a tenth in suffix-array
lookups and an eighth in the 8-bit extension. Suffix-array lookups stay on the
CPU: the sampled suffix array would add about 4 GiB of GPU memory to the
occurrence table's 6 GiB.

### wgse-minimap2 on Linux x86-64

`wgse-minimap2` also builds on Linux x86-64. It has
the same output-identical changes as on a Mac (faster index loading and
building, seed prefetching and the anchor sort), with upstream's x86-64
base-level alignment: the SSE4.1 and SSE2 `ksw_extd2` kernels, picked at run
time as upstream's build does. Its tests compare it with unmodified upstream
minimap2 2.31 as on a Mac (`tests/upstream.sh`).

Its conda package (`packaging/wgse-minimap2`, 0.4.0, with the CUDA backend
below) is built by the same workflow and published next to `wgse-bwa` in the
`linux-aligners-*` prereleases, from the release after `linux-aligners-2`.
On `PATH`, `align` and `realign` run it in minimap2's place. For long reads
they add `--backend auto`, so it uses the GPU when one is usable
([docs/hardware.md](../docs/hardware.md#nvidia-gpus-without-parabricks-wgse-bwa---cuda)).
Install it as `wgse-bwa-mem3` above, in its own environment, with that
release's tag:

```bash
TAG=linux-aligners-N PKG=wgse-minimap2-0.4.0-linux64_wgse_0.conda
gh release download "$TAG" -R WGSExtract/wgsextract-cli -p "$PKG" --clobber
gh release view "$TAG" -R WGSExtract/wgsextract-cli --json assets \
  --jq '.assets[] | "\(.digest | ltrimstr("sha256:"))  \(.name)"' |
  grep -F "  $PKG" | sha256sum -c &&
conda create -y -n wgse-minimap2 -c conda-forge "libgcc>=14" "libstdcxx>=14" \
  "libzlib>=1.3.2,<2" &&
conda install -y -n wgse-minimap2 "./$PKG" &&
export PATH="$(conda run -n wgse-minimap2 printenv CONDA_PREFIX)/bin:$PATH"
```

On the RTX 3090 machine above (Ryzen 5 5600X), with 8 threads, `.mmi` indexes
and two interleaved rounds (means), the records of all three were identical:

| Reads | Bioconda minimap2 2.31 | Plain build | `wgse-minimap2` | Peak RSS | Records |
| --- | ---: | ---: | ---: | ---: | --- |
| HiFi, 40k reads, 0.69 Gbp (`map-hifi`) | 43.4 s | 43.2 s | 32.2 s (−26%) | 14.3–14.6 GiB | `661f00b5ed4b82c1` |
| ONT, 47k reads, 0.92 Gbp (`map-ont`) | 113.5 s | 112.3 s | 112.0 s (−1%) | 11.4–12.0 GiB | `895d325921dc2bf2` |
| full-5m tier (`sr`, `bench.sh`) | 176.3 s | 173.7 s | 157.1 s (−11%) | 11.3 GiB | `8d527463b1dad57d` |

The HiFi reads are the first 40k of PacBio's public Revio HG002 run, as above;
the ONT reads are HG002's (R10.4.1) that overlap a 750 kb window of each of
chr1–22 and X. The `sr` records match `expected.tsv`, as do the small tier's.
HiFi gains from loading the index on several threads; mapping itself costs the
same CPU time. A `perf` profile at 4 threads puts `ksw_extd2` (base-level gap
fills) at 47% of the HiFi CPU time and 16% of ONT's, where chaining (DP and
RMQ, 50%) and anchor sorting dominate, so GPU gap fills pay off mostly on
HiFi.

With `-DWGSE_CUDA=ON`, `--cuda` (`--backend cuda`, experimental) runs the gap
fills on an NVIDIA GPU as `--metal` does on a Mac, with the same exact-input
matching. `--backend auto` uses the GPU when it can, and otherwise aligns on
the CPU.

- **Shared engine:** `ksw_host.cpp` is the engine both GPU builds share. It
  runs reads as fibers on each `-t` thread (an x86-64 ELF context switch
  alongside the arm64 one), queues their fills and dispatches the batches.
  `ksw_engine.h` is the device interface: Metal (`metal_ksw.mm`) and CUDA
  (`ksw_cuda.cu`).
- **Kernel:** `ksw_fill.cuh` is the Metal kernel ported to CUDA, one warp per
  fill, in 8- and 16-bit variants. Fills whose int8 differences wrap go to the
  CPU, as on a Mac.
- **Memory and failures:** each of the two dispatchers allocates its buffers
  up front: about 0.35 GiB of GPU memory and 0.1 GiB of pinned host memory. `--cuda`
  needs those, with 1 GiB spare, or it refuses to start. A watchdog bounds every
  wait for the GPU. A failed or hung batch is finished on the CPU with the same
  output; `--cuda` then exits 1, and `--backend auto` exits 0.
- **Tests:** `tests/ksw_gpu_fuzz.cpp` compares the device with upstream's
  `ksw_extd2_sse` on random batches, through the same interface for Metal and
  CUDA. `tests/upstream.sh` runs its GPU cases with whichever GPU the build has.

On the same machine and data (8 threads, `.mmi` indexes, two interleaved
rounds, means; records identical):

| Reads | Bioconda minimap2 2.31 | `wgse-minimap2` | `wgse-minimap2 --cuda` | CPU time (CPU → `--cuda`) |
| --- | ---: | ---: | ---: | ---: |
| HiFi, 40k reads (`map-hifi`) | 45.6 s | 32.5 s | 20.4 s (−37%) | 207 → 120 s (−42%) |
| ONT, 47k reads (`map-ont`) | 112.3 s | 111.6 s | 100.7 s (−10%) | 808 → 729 s (−10%) |

`--cuda` used 0.94 GiB of GPU memory and 0.8 GiB more RAM. The GPU computed
all but a few hundred of the HiFi set's 3.45M fills (the rest by CPU threads
with no read left to map), at about 12 G cells/s.

## Building

Requires Xcode command-line tools and CMake 3.24 or later. Upstream sources are
downloaded at pinned SHA-256 hashes.

```bash
cmake -S native -B out/native -DCMAKE_BUILD_TYPE=Release
cmake --build out/native -j
ctest --test-dir out/native --output-on-failure
cmake --install out/native --prefix out/native-prefix
```

On Linux the same commands build `wgse-bwa` with a C/C++ compiler, CMake and
zlib, and on x86-64 `wgse-bwa-mem3` (it also needs `make` for htslib;
`-DWGSE_BWA_MEM3=OFF` leaves it out) and `wgse-minimap2` (`-DWGSE_MINIMAP2=OFF`
leaves it out) too. `-DWGSE_CUDA=ON` adds the CUDA backends,
which need the CUDA toolkit (`nvcc` and the static runtime, for example conda-forge's `cuda-nvcc` and
`cuda-cudart-static`). It builds for Turing to Hopper GPUs, plus PTX for newer
ones; `-DCMAKE_CUDA_ARCHITECTURES` overrides this. A GPU the build has no
kernels for (such as Pascal or Volta, by default) is never selected, and
`capabilities --json` reports it unavailable and unsupported. The GPU parity tests run with
whichever GPU backend the build and machine have. CI's runners have no NVIDIA GPU:
`.github/workflows/linux_aligners.yml` compiles the CUDA backends (`cuda-build`)
but can't run their tests.

`-DBUILD_TESTING=OFF` builds the release executables without tests or the
hidden GPU tuning flags. The public `--dense-sa on|off` switch remains
available; `--dense-sa force`, which bypasses its memory check, is
benchmark-only. `bwa_release_dense_sa` also checks alignment records with a
production parser, so benchmark-enabled builds cannot hide missing public
options. Metal tests are skipped when no Metal device is
available. `.github/workflows/macos_aligners.yml` builds, tests and packages
all four executables, and publishes them when run with a `macos-aligners-N` tag.

## Benchmarking on GitHub runners

`.github/workflows/aligner_bench.yml` times both aligners and checks their
alignments on GitHub-hosted Macs. Standard arm64 runners (`macos-15`,
`macos-26`, `macos-latest`) are virtual M1s with 3 cores and 7 GiB of RAM, so
they also show how the aligners behave on small Macs. Run it from Actions >
Aligner Bench, or:

```bash
gh workflow run aligner_bench.yml -f variants=main,my-branch -f tiers=small,medium \
    -f configs=bwa:cpu,bwa:metal -f rounds=3
gh run watch "$(gh run list --workflow aligner_bench.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

- `variants`: git refs to build, or `macos-aligners-N` releases to download;
  the first is the baseline that the others are compared with.
- `tiers`: the datasets in `bench/tiers.tsv`. `small` is chr20, `medium` is
  chr1–3 and `full` is hs38, each with HG002 pairs; `full-5m` is hs38 with
  5M pairs. The workflow downloads them from the `aligner-bench-data-1`
  release and checks their sha256 (`bench/data.tsv`).
- `configs`: `TOOL:BACKEND[:tN][:KN][:probe][:+ARG...]`, for example
  `bwa:metal:t2` or `mem2:metal:probe` (BACKEND is `cpu`, `metal`, `cuda` or
  `auto`; `cuda` needs a Linux machine with an NVIDIA GPU, which hosted runners
  lack); by default each tier's configs in
  `bench/tiers.tsv` (only `wgse-bwa --metal` on `full`). A failure of a `probe`
  config, such as an out-of-memory kill, is reported but does not fail the job.
  `+ARG` passes an extra argument to the aligner's `mem` command; such runs
  are checked only against runs with the same arguments.
- `runners`: runner labels; larger runners such as `macos-15-xlarge` work when
  the organisation pays for them.
- `rounds`, `threads`, `balloon_mib` (RAM to wire for the whole job, to act
  like a smaller Mac; a runner can wire about 4.9 GiB), and timeouts.

Each runner and tier is one job. It runs every config with every variant in
turn, reversing the order on even rounds so that the variants share the
runner's conditions, and records the hardware (memory, cores, Metal device),
each run's time, CPU time, peak RSS and footprint, page faults, swap,
memory pressure, GPU work, CPU fallbacks and exit status or signal, and a hash
of its alignments. Alignments must match `bench/expected.tsv` (computed with
the CPU backends, which are upstream's code) and each other (with `K0`, only
runs with the same threads, since the batches then depend on them); a mismatch
or a failed run fails the job. The job summary has a table per job and the report
job one for the whole run; `bench-run-*` artifacts hold the logs. Its
"vs first" column is the median change against the first variant over runs in
the same round, with their range: runs on hosted runners vary by 5–20%, so
compare variants in one job with three or more rounds. Several runs can be
dispatched at once.

`bench/bench.sh` also runs locally, on macOS or Linux:

```bash
native/bench/bench.sh fetch --data out/bench-data --tier small
native/bench/bench.sh run --data out/bench-data --tier small --out out/bench \
    --bin main=out/native-prefix/bin --bin test=../other/out/native-prefix/bin --rounds 3
```

A `--bin` directory may also hold upstream executables (`bwa`, `bwa-mem2`,
`bwa-mem3`, `minimap2`, for example from a Bioconda environment); they run the
`cpu` configs of their tool. `mem3` runs BWA-MEM3 on the BWA-MEM2 index and
`mm2` runs `minimap2 -ax sr` on `<ref>.fa.sr.mmi`, which the data release
does not have: build it next to the other index files with
`minimap2 -x sr -d <ref>.fa.sr.mmi <ref>.fa`.

On Linux the memory columns come from `/proc`: "Pressure" is the kernel's
memory pressure (PSI "some", 10-second average), "Min free" is
`MemAvailable`, the compressor is zswap's pool and a run the OOM killer
ended counts as jetsam (its log needs read access to the kernel journal).
Linux has no footprint or page-in counters, so those columns are empty, and
`--power` is macOS only.

`--power` also measures each run's energy with `powermetrics`, which needs
root: `--power-cmd` gives the command that runs it without a password prompt
(by default `sudo -n powermetrics`). `bench/power.py` samples CPU, GPU and ANE
energy every second, waits `--power-idle` seconds (default 60) before each run
to measure the idle power, and writes `power.txt` beside the run's other
files; `results.tsv` and the summary then have the idle power, the CPU, GPU
and package energy of the run, and the package energy above idle. With
`--power-quiet WATTS` it waits up to 30 minutes for an idle window whose mean
package power is at most WATTS, so that other work on the machine does not
count towards a run.

The power command must keep working without a prompt for the whole bench,
which can take hours. A cached `sudo` ticket expires after 5 minutes, so the
check at the start can pass and later runs still fail. Use a sudoers rule
instead, such as `you ALL=(root) NOPASSWD: /usr/bin/powermetrics`, or a helper
that keeps a privileged session open. If sampling fails before a run, the
aligner still runs: its row records power `failed`, the bench prints a
warning, and the summary leaves the run out of the energy columns. For example:

```bash
native/bench/bench.sh run --data out/bench-data --tier full-5m --out out/energy \
    --bin bioconda=.pixi/envs/default/bin --bin wgse=out/native-prefix/bin \
    --configs bwa:cpu,bwa:metal,mem2:cpu,mem2:metal,mem3:cpu,mm2:cpu \
    --threads 9 --rounds 2 --power
```

`bench/make_data.sh` builds the datasets and expected hashes from hs38 and
HG002 reads. It now skips supplementary records when it picks the pairs of a
tier, which `aligner-bench-data-1` predates (a few hundred of its pairs were
picked by a supplementary record), so a rebuild is published under a new tag.

What the hosted 7 GiB runners showed (issue #364):

- `--metal` is 4–5 times faster than the CPU backends on their virtual GPU:
  on `medium`, about 27 s against 130 s for `wgse-bwa` and 30–38 s against
  130–150 s for `wgse-bwa-mem2`.
- On `full` (hs38), `wgse-bwa --metal` finished all six runs, aligning
  200k pairs in 95–200 s with a 6 GiB footprint; its Metal buffers keep the
  index in RAM. The `wgse-bwa` CPU backend finished one run in six (167 s).
  The others, and both backends of `wgse-bwa-mem2` (11–17 GiB), paged through
  the memory compressor and swap until they were stopped after 20–45 minutes.
  Before `wgse-bwa-mem2` 0.4.1 neither tool warned about this; 0.4.1 warns on
  stderr when the Mac has less memory than its estimate of the run's peak.
- `wgse-bwa-mem2 --metal` on hs38 seeds on the CPU on GPUs whose largest
  buffer is under 5.8 GiB, as on these runners; 0.4.1 warns about this and
  reports it in `capabilities --json hs38.fa`.
- On `full-5m`, `wgse-bwa --metal` took 66 and 87 minutes and its alignments
  matched. In a third run the virtual GPU hung after about 30 minutes;
  `wgse-bwa` fell back to the CPU and then thrashed. Keep
  `run_timeout_minutes` at its default of 120 or more for this tier.

## Licences

`wgse-bwa` combines BWA (GPL-3.0) with the code in `bwa/` and `metal-common/`
(GPL-3.0-or-later); its Metal seeding, locate, mate-rescue and extension kernels are
adapted from BWA's MIT-licensed `bwt.c`, `bwamem.c`, `bwamem_pair.c` and
`ksw.c` (`METAL-SEEDING-LICENSE`).
`wgse-bwa-mem2` combines BWA-MEM2 (MIT), sse2neon
(MIT) and safestringlib (MIT) with the code in `bwa-mem2/` (GPL-3.0-or-later,
like this repository). `wgse-bwa-mem3` combines bwa-mem3 (MIT), sse2neon
(MIT), libsais (Apache-2.0), mimalloc (MIT), zlib-ng (zlib), libdeflate (MIT)
htslib (MIT/Expat and modified BSD) and htslib's bundled htscodecs (BSD) with
the code in `bwa-mem3/` (GPL-3.0-or-later); its CUDA build's mate rescue runs
`wgse-bwa`'s kernel, adapted from BWA's MIT-licensed `ksw.c`
(`BWA-KERNELS-LICENSE`).
`wgse-minimap2` combines minimap2 (MIT) and sse2neon
(MIT) with the code in `minimap2/` (GPL-3.0-or-later). Its sketching code is
adapted from minimap2's `sketch.c` (`MINIMAP2-LICENSE`) and its radix sort from
minimap2's `ksort.h`, whose own MIT notice ships as `KSORT-LICENSE`. Each package
ships its licences, including this repository's GPL-3.0 text as `WGSE-LICENSE`,
under `share/licenses/`, and the corresponding source is this directory plus the
pinned upstream archives in the CMake files.
