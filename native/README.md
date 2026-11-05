# Apple Silicon aligner rebuilds

Optional macOS arm64 (macOS 13+) rebuilds of the two short-read aligners
WGS Extract uses, published as `macos-aligners-*` prereleases. The
`wgsextract` CLI does not use them yet: they are offered for testing until
they are qualified on full genomes (#364).

| Executable | Upstream | Notes |
| --- | --- | --- |
| `wgse-bwa` | BWA 0.7.19 | Unmodified source, native arm64 build. Low memory: about 5 GB for a human index plus a little per thread. |
| `wgse-bwa-mem2` | BWA-MEM2 2.3 | arm64 build through sse2neon. CPU by default; `--metal` is **experimental**. Needs much more memory than BWA (see below). |

Both write the same alignments as their upstream release; only the `@PG`
version gains a `-wgse-<version>` suffix.

## Usage

```bash
wgse-bwa index hs38.fa
wgse-bwa mem -t 8 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam

wgse-bwa-mem2 index hs38.fa
wgse-bwa-mem2 mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem2 --metal mem -t 9 hs38.fa R1.fastq.gz R2.fastq.gz > out.sam
wgse-bwa-mem2 capabilities --json
```

Options before the command select the BWA-MEM2 backend:

- default (`--backend cpu`): BWA-MEM2's own SIMD code;
- `--metal` (`--backend metal`): batches seed extension across worker threads
  onto the GPU, keeping small batches on the CPU. Fails if Metal is unusable;
- `--backend auto`: like `--metal`, but falls back to the CPU without Metal.

With `--metal` the backend prints GPU and CPU batch counts to stderr.

## Memory

BWA-MEM2 trades memory for speed. Building its human index needs roughly
60–90 GB of RAM, and aligning needs the 15–20 GB index in memory. BWA indexes a
human genome in about 5 GB and aligns in about 5 GB plus a few hundred MB per
thread, which fits ordinary desktops. `--metal` adds under 100 MB of GPU
buffers.

## Performance

Measured on an M1 Pro (8P+2E cores, 32 GB) with 10.6M read pairs and a small
reference (chr20 fragment, Y, MT) whose whole index fits in cache. Full-genome
numbers will differ; all runs produced identical alignments.

| Aligner | Threads | Time | vs packaged build |
| --- | --- | --- | --- |
| Bioconda bwa 0.7.19 | 9 | 53.2 s | — |
| `wgse-bwa` | 9 | 51.9 s | −2.5% |
| Bioconda bwa-mem2 2.3 | 8 | 45.7 s | — |
| `wgse-bwa-mem2` | 9 | 35.0 s | −23.5% |
| `wgse-bwa-mem2 --metal` | 9 | 31.0 s | −32.2% |

## Building

Requires Xcode command-line tools and CMake 3.24 or later. Upstream sources are
downloaded at pinned SHA-256 hashes.

```bash
cmake -S native -B out/native -DCMAKE_BUILD_TYPE=Release
cmake --build out/native -j
ctest --test-dir out/native --output-on-failure
cmake --install out/native --prefix out/native-prefix
```

`-DBUILD_TESTING=OFF` builds the release executables without tests or the
hidden GPU tuning flags. Metal tests are skipped when no Metal device is
available. `.github/workflows/macos_aligners.yml` builds, tests and packages
both executables, and publishes them when run with a `macos-aligners-N` tag.

### FM-index seeding feasibility benchmark

With `BUILD_TESTING=ON`, `bwa/seeding_benchmark` compares Metal against BWA
0.7.19's actual `mem_collect_intv` on one and nine CPU threads:

```bash
out/native/bwa/seeding_benchmark hs38.fa reads.fastq.gz \
    --reads 32768 --batch 8192 --repeat 3
```

It needs an existing `.bwt` index and FASTQ (plain or gzip); no suffix array is
needed to collect intervals. All three default seeding passes, interval order,
and all four 64-bit interval fields must agree exactly. A warm-up precedes
timing. `gpu_s` includes packing, dispatch, waiting, unpacking and CPU fallback;
`kernel_s` is Metal command execution time. Index loading, shader compilation,
FASTQ parsing and verification are excluded from the timed sections.

The prototype uses one read per GPU thread, a shared page-aligned no-copy BWT
buffer and bounded per-read scratch. Reads longer than 512 bases, scratch
overflow (128 intervals) or output overflow (256 intervals) fall back to upstream
CPU collection and are counted explicitly. `--fallback-threads` selects CPU
fallback workers (default nine); thus `gpu_s` measures the hybrid path, not a
GPU-only speedup. `fallback_s` reports the separate fallback phase. `--group`
selects the threadgroup size. `--verify-only` omits timing; `--scratch-limit` and
`--output-limit` lower the bounds to exercise fallback or tune divergence.
`metal_seeding_parity` covers unique, repetitive, ambiguous, short and overlength
reads, partial batches, overflow, and tiny indexes with missing DNA symbols.
Missing Metal devices return 77; shader or parity failures are errors.

This is a feasibility tool, not a `wgse-bwa --metal` backend. Neither production
aligner is changed by it. The small `metal-common/` runtime provides reusable
device/queue setup, runtime shader compilation, shared buffers and dispatch;
the existing BWA-MEM2 extension backend is deliberately left untouched.

`--concurrent` additionally partitions the input into disjoint CPU9 and GPU
workloads sharing the same physical BWT. `--gpu-percent` controls the GPU share
(default 50). `combined_s` includes launch/join, packing, GPU dispatch, unpacking,
CPU fallback and the slower partition's tail; `combined_rps` counts each input
read once. Partition construction and verification are excluded. CPU and GPU
completion times expose imbalance rather than summing isolated throughputs.
Fallback workers may briefly oversubscribe the nine CPU workers; use
`--fallback-threads 1` as a low-contention control.

## Licences

`wgse-bwa` is GPL-3.0 (BWA). `wgse-bwa-mem2` combines BWA-MEM2 (MIT), sse2neon
(MIT) and safestringlib (MIT) with the code in `bwa-mem2/` (GPL-3.0-or-later,
like this repository). Each package ships its licences under
`share/licenses/`, and the corresponding source is this directory plus the
pinned upstream archives in the CMake files.
