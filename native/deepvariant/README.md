# wgse-deepvariant: DeepVariant on Apple Silicon

`wgse-deepvariant` is a self-contained Apple Silicon (macOS 14+, arm64) build
of [DeepVariant](https://github.com/google/deepvariant) 1.10.0. It runs
make_examples and postprocess_variants natively on the CPU and call_variants on
the GPU (Metal, FP32), without Docker, TensorFlow or Python. Google publishes
DeepVariant for x86-64 only, and its image can't run under Docker Desktop's
emulation on Apple Silicon (#665). Only the WGS model is qualified.

It is the native port of DeepVariant by IPNP-BIPN
([IPNP-BIPN/deepvariant](https://github.com/IPNP-BIPN/deepvariant),
`feature/apple-silicon-native-v2` at `0b7b9c5`, proposed upstream as
google/deepvariant#1085) with the patches in [`patches/`](patches):

- **0001** makes its WGS make_examples match Google's 1.10.0 image: the
  realigner keeps reads at their original alignment as upstream does
  (`--normalize_reads` is off except for the pangenome model), alleles are
  counted only from realigned reads that still overlap the region (upstream's
  `in_memory_sam_reader.query(region)`), and ties in the realigner's haplotype
  order break as GCC's `std::sort` breaks them. It also writes gzip-compressed
  examples (`examples.tfrecord@N.gz`), as upstream does: uncompressed ones took
  about 12 GB for chr20 alone.
- **0002** builds from one prefix of static dependencies instead of Homebrew.
- **0003** adds `run --dry_run=true`, which prints the make_examples,
  call_variants and postprocess_variants commands in run_deepvariant's format,
  so a caller can run and resume them one by one as it does Google's
  (single-sample runs only; trio, somatic and pangenome runs refuse it).
- **0004** reads `--reads` from https URLs and accepts `.csi` BAM indexes.
  Reading a remote BAM partition by partition fetches far more than the BAM
  (33 GB in 14 minutes for 20 Mb of chr1), so for more than a small region
  copy the reads first (`samtools view -b URL REGION`).
- **0005** makes postprocess_variants call one contig at a time: it held every
  site of the run in memory, tens of GB for a whole genome.
- **0006** lets `--regions` name BED (or gzipped BED) files, so a selection of
  any size fits on the command line, and merges the regions into one set in
  reference order, as upstream does.
- **0007** always trims reads to the pileup window, as Google's make_examples
  does: it sets `trim_reads_for_pileup` whenever `--alt_aligned_pileup` is set,
  and that flag always holds a mode name, `none` included. Without trimming, a
  read whose only overlap with the window was a long deletion became a blank
  row ahead of the reads that cover the window. On HG003's whole genome
  (wgse-deepvariant 0.1.0) this changed 20 low-quality records at 6 loci, none
  in GIAB's confident regions.

## Same results as Google's image

On HG002 chr20 (the DeepVariant case study's NovaSeq 35x BAM, GRCh38), against
`google/deepvariant:1.10.0` on x86-64:

| | Official x86-64 | wgse-deepvariant |
| --- | ---: | ---: |
| Candidates (and small/big-model routes) | 210,390 | 210,390, identical |
| Sites, genotypes, FILTER | | identical |
| QUAL | | 5,036 sites differ, by at most 0.5 |
| GQ | | 228 sites differ, by at most 2 |

So hap.py against GIAB v4.2.1 gives the same counts (SNP F1 0.997409, INDEL
F1 0.996074). The QUAL and GQ differences come from call_variants: Metal's FP32
convolutions sum in a different order from TensorFlow's on x86-64, as the
Linux ARM64 build's do (docs/deepvariant-arm64.md: QUAL at most 0.5, GQ at
most 2 on chr20).

Before the patches the port found 229 more candidates and differed at 894
sites (66 genotypes, 56 filters), all outside GIAB's confident regions.
call_variants on the GPU (FP32) gives the same calls from the same examples;
FP16 on the Neural Engine does not, so it isn't used. See #647 for the
measurements.

## Speed and memory

HG002 chr20 on an M1 Pro (10 cores, 32 GB), 10 threads, each stage on its own:

| Stage | Time | Peak memory |
| --- | ---: | ---: |
| make_examples | 189 s | < 1 GB |
| call_variants (GPU, batch 1024) | 104 s | 4.3 GB resident, 12.9 GB footprint |
| postprocess_variants | 2 s | 0.6 GB |
| Intermediate files | | 0.8 GB |

Google's image took 287 + 409 + 12 s on a 12-thread Ryzen 5600X. make_examples
scales with threads (2: 583 s, 4: 294 s, 8: 201 s, 10: 167 s before gzip); the
calls are identical whatever the thread count. call_variants' batch size
follows the Mac's memory (512 on 16 GB) unless `--batch-size` sets it.

On a **16 GB base M1**, release **0.1.1** runs the same chr20 through the CLI
in **409 s** at six threads and batch 512, with **2.24 GB peak RSS** and
**6.56 GB peak GPU-inclusive footprint**, without additional swap-outs.
RTG SNP/indel F1 is **0.997409 / 0.995993** (RTG, not the hap.py figures above).
See the [16 GB measurements](../../docs/apple-silicon-16gb.md#native-deepvariant)
for the version-labelled whole-genome timing projection; a whole genome was
measured with 0.1.0, **not** 0.1.1.

## Installing

Download a `macos-deepvariant-N` prerelease into an empty directory with `gh`
(which avoids the browser's quarantine flag on these ad-hoc-signed
executables), and check the archives against the published digests:

```bash
mkdir wgse-deepvariant && cd wgse-deepvariant
gh release download macos-deepvariant-2 -R WGSExtract/wgsextract-cli
shasum -a 256 -c SHA256SUMS
```

Continue only if every archive reports `OK`. Then unpack both and put the
binary's `bin` folder on `PATH` (add the `export` line to your shell profile to
keep it):

```bash
for archive in wgse-deepvariant-*.tar.gz; do tar -xzf "$archive"; done
export PATH="$PWD/$(ls -d wgse-deepvariant-*-macos-arm64)/bin:$PATH"
wgse-deepvariant --version
```

The weights folder, `wgse-deepvariant-weights-1.10.0-wgs`, is what
`--checkpoint` (`wgs.dvw`) and `--small_model_path` (`wgs_small_weights`)
name:

```bash
w=wgse-deepvariant-weights-1.10.0-wgs
wgse-deepvariant run --model_type=WGS --ref=ref.fa --reads=sample.bam \
  --checkpoint=$w/wgs.dvw --small_model_path=$w/wgs_small_weights \
  --output_vcf=sample.vcf.gz --intermediate_results_dir=work --num_shards=8
```

## Weights

The weights are Google's DeepVariant 1.10.0 WGS model and small model from
`google/deepvariant:1.10.0` (the image digest is in the workflow),
repackaged by [`convert_weights.py`](convert_weights.py): the big model's
float32 variables in the port's `.dvw` layout and the small model's dense
layers as `.npy` files. The conversion is deterministic; the release build
checks its output against `weights-sha256.txt`.

## Building

The build downloads and checks each dependency (abseil, protobuf, re2, htslib
with libdeflate and liblzma, Boost headers and the port), builds them as static
libraries and links a binary that uses only macOS's own libraries:

```bash
cmake -S native/deepvariant -B out/dv-build -DCMAKE_BUILD_TYPE=Release
cmake --build out/dv-build -j "$(sysctl -n hw.ncpu)"
cmake --install out/dv-build --prefix out/dv-prefix
native/deepvariant/tests/smoke.sh "$PWD/out/dv-prefix/bin/wgse-deepvariant" <weights> out/dv-smoke
```

It needs the macOS 26 SDK (Xcode 26 or its command-line tools), and CMake 3.27+; the smoke test
needs samtools. The macOS DeepVariant workflow
(`.github/workflows/macos_deepvariant.yml`) converts the weights in Google's
image, builds and smoke-tests the binary and, when run with a release tag,
publishes both with a `SHA256SUMS`.

## Licences

DeepVariant and the port are BSD-3-Clause (Google LLC); Google distributes the
1.10.0 models under the same licence, which the weights archive includes. The
binary's archive has the licences of everything built into it in
`share/licenses/wgse-deepvariant`: abseil (Apache-2.0), protobuf, re2 (BSD-3),
htslib and htscodecs (MIT/BSD), libdeflate (MIT), liblzma (0BSD), Boost (BSL-1.0),
libssw and sse2neon (MIT).
