# Linux CUDA PairHMM prototype

This is an experimental GATK **4.6.2.0 / GKL 0.8.11** native-resource
sidecar, not Parabricks. On the RTX 3090, GKL/CUDA decoded gVCF and
genotyped VCF records matched exactly on HG002 chr20 and the public
HG00096 full mapped reference. See the
[measurements and limits](../../docs/hardware.md#experimental-native-cuda-pairhmm-for-gatk).
This is not a general release, truth-set, or controlled speedup qualification.

## Interface and arithmetic

The jar supplies `com/intel/gkl/native/libgkl_pairhmm{,_omp}.so` through
GKL's existing native loading interface. It does not replace GATK classes,
the native-binding Java API, GKL utilities or Smith-Waterman. It therefore
uses the same JNI contract as the
[Arm64 GKL sidecar](../../packaging/arm64/gkl/README.md). It requires an
AVX-capable Linux x86-64 CPU for stock GKL's platform gate and CPU fallback.

Each read/haplotype pair is a CUDA thread block, processing anti-diagonals.
The host builds GKL's own transition/emission tables. Separate, ordered
multiply/add operations and separate terminal-row M/X accumulation preserve
the GKL arithmetic rather than substituting a mathematically similar model.
CUDA contraction is disabled; float subnormals are flushed. Raw probabilities
return to the original GKL JNI loop for its original float-to-double rescue
and host `log10f`/`log10`.

Reads longer than 1,024 bases, haplotypes longer than 65,536, double-precision
requests and batches exceeding 128 MiB of packed input stay on the CPU.
Unavailable drivers/devices, incompatible kernels, insufficient free device
memory and CUDA errors disable the GPU for the remaining process and use
the original GKL CPU computation. Memory is checked before allocation,
including a 256 MiB device reserve. The device with most available memory
among compatible GPUs is selected; small consumer GPUs do not face a
Parabricks-style 16 GiB minimum. Kernels target Turing and newer (SM 7.5+).
The shutdown log counts actual GPU-computed read/haplotype pairs.

## Build (explicit download only)

Install the prerequisites yourself: Linux x86-64, a CUDA 12+ Toolkit and
supported C++ compiler, OpenMP, a JDK 17+ (`java`, `javac`, `jar`), Python 3,
curl, GNU coreutils and tar. Runtime needs an NVIDIA driver compatible with
the Toolkit used to build, plus libstdc++, libgcc and libgomp.

```bash
bash native/pairhmm/build-linux-cuda.sh \
  out/pairhmm/source /path/to/gatk-package-4.6.2.0-local.jar out/pairhmm/package \
  --download
```

`--download` confirms fetching the public, SHA-256-pinned GKL 0.8.11 source
from GitHub. Without it, a missing source archive is an error. No sample
information is sent. No dependency is installed. Subsequent builds work
offline with the verified source archive.

Opt in through the CLI (also validates the local sidecar and GATK version):

```bash
wgsextract vcf gatk --input reads.bam --ref reference.fa \
  --pair-hmm-cuda out/pairhmm/package/gkl-native-wgse-linux-cuda.jar \
  --reference-evidence gvcf --outdir out/calls
```

This is a local option, not available over the server API. It cannot be
combined with Parabricks `--gpu`, `--scatter`, `--pair-hmm-neon` or
Ultima's flow likelihood model, which does not use this PairHMM.

For an individual standalone GATK invocation, put this resource-only jar
on the JVM's bootstrap append path:

```bash
gatk --java-options "-Xbootclasspath/a:$PWD/out/pairhmm/package/gkl-native-wgse-linux-cuda.jar" \
  HaplotypeCaller -R reference.fa -I reads.bam -O out/calls.g.vcf.gz -ERC GVCF
```

The upstream `gatk` launcher splits `--java-options` on whitespace; keep the
sidecar path free of whitespace and `:`. Its log must name this jar when
loading `libgkl_pairhmm_omp.so`, and report a nonzero GPU-pair count after a
nontrivial run. Otherwise it is not evidence of CUDA execution.
Unmodified invocations stay on stock GKL. In particular,
`wgsextract vcf gatk --gpu` still means Parabricks.

## Numerical qualification

Run `tests/parity.sh GATK_JAR SIDECAR_JAR OUT_DIR [BATCHES] [THREADS]` with
an available GPU. It compares raw likelihood bits, not rounded decimal
likelihoods, in separate JVMs using stock and sidecar resources. It requires
a nonzero actual GPU-pair count, and separately verifies exact CPU fallback
with CUDA's process-local device mask hiding the GPU.
Synthetic cases include short/stripe/block boundaries, N bases,
varying gap continuations, unequal haplotype lengths, low qualities,
underflow/double rescue, explicit double precision and long-read CPU fallback.
Repeat with one and multiple native threads.

On the RTX 3090, 32,000 synthetic pairs in float and double modes matched
stock GKL likelihood bits at both one and four native threads, including
the unavailable-GPU fallback. The CUDA pass computed 63,936 actual GPU pairs
per thread configuration (including repeat checks). A real 5 kb
HaplotypeCaller fixture with a heterozygous SNP, insertion and deletion also
produced identical complete VCF and gVCF bodies through the CLI.

Reproduce the SNP/insertion/deletion case with
`tests/haplotypecaller.sh GATK_JAR SIDECAR_JAR OUT_DIR [THREADS]`.
It needs samtools and bcftools in addition to the JDK, and checks every
VCF/gVCF body field as well as the expected three heterozygous calls.

### Reproduce the calling comparison

Use the same local alignment, matching FASTA, dictionary and index for
every backend. The measured runs used standalone GATK, one monolithic
HaplotypeCaller per backend, `-Xmx4g -XX:ActiveProcessorCount=1`, and:

```text
HaplotypeCaller --sample-ploidy 2 --native-pair-hmm-threads 1 \
  --pair-hmm-implementation AVX_LOGLESS_CACHING_OMP \
  -ERC GVCF -GQB 10 -GQB 20 -GQB 30 -GQB 60
```

Append the sidecar on the JVM bootstrap path only for CUDA. For the
chr20 Java comparison omit the sidecar and select `LOGLESS_CACHING`.
Use `-L chr20` with the HG002 PCR-free 35× BAM and GRCh38 no-alt reference.
For public `HG00096.final.cram` use the matching `hs38DH.fa` and a BED
containing `[0, length)` for every sequence in FASTA index order. For this
CRAM with HTSJDK 4.2.0, pass the same separate mapped-only CRAI to both
backends with `--read-index`: keep all 80,777 original mapped entries
unchanged and omit the nine unmapped entries. Do not edit the original
index. The mapped interval policy intentionally excludes unmapped reads;
regional reader checks sampled mapped-read preservation rather than
exhaustively proving it. Fixed ploidy 2 is a comparison baseline, not
sex-chromosome ploidy guidance.

Run `GenotypeGVCFs` separately on each caller's gVCF against the same
reference, without prepending the sidecar. Decode both gVCFs and both
final VCFs (`bcftools view -H`), check successful decoding, then compare
each complete record body directly. Also check indexes, sample/GT and
reference coverage and verify that the CUDA caller's GPU pair count is
nonzero. Do not compare compressed-file checksums to infer record parity.
The [full measurement record](https://github.com/WGSExtract/wgsextract-cli/issues/1131#issuecomment-7071143485)
includes reference coverage, counts and resource observations.

## Prior art and licensing

- [IntelLabs/GKL 0.8.11](https://github.com/IntelLabs/GKL/tree/0.8.11):
  MIT-licensed JNI, tables, AVX and AVX-512 CPU kernels, reused at a pinned
  source digest with its licence included in the sidecar.
- [gpuPairHMM](https://github.com/asbschmidt/gpuPairHMM/tree/3965d2d3313682532ebb53cceec4876cb4da7b24):
  Apache-2.0. Its GPU kernels fix gap-continuation probability at 0.1, use
  different match-to-match transition arithmetic and explicit fused operations.
  It is useful prior art, not a numerically compatible drop-in. No code copied.
- [NVIDIA NVBIO](https://github.com/NVlabs/nvbio): BSD-3-Clause alignment
  primitives, not a GATK native-binding forward PairHMM implementation.
- New files in this directory are MIT-licensed (see `LICENSE`), as are the
  existing project's new NEON kernels. CUDA's redistributable runtime remains
  under NVIDIA's licence; the Toolkit EULA ships with the sidecar.

No Parabricks implementation or proprietary kernel source is used.
