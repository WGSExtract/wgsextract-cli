# Linux CUDA PairHMM prototype

This is an experimental GATK **4.6.2.0 / GKL 0.8.11** native-resource
sidecar, not Parabricks. It is not a qualified release: HG002 chr20 calls,
gVCF blocks and whole-genome timing must pass before recommending it.
Only [preliminary 1 Mb timings](../../docs/hardware.md#experimental-native-cuda-pairhmm-for-gatk)
are available, not an established end-to-end acceleration claim.

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

This cannot be combined with Parabricks `--gpu`, `--scatter` or Ultima's
flow likelihood model, which does not use this PairHMM.

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
These tests do not establish HG002 chr20 or whole-genome acceptance.

Reproduce the SNP/insertion/deletion case with
`tests/haplotypecaller.sh GATK_JAR SIDECAR_JAR OUT_DIR [THREADS]`.
It needs samtools and bcftools in addition to the JDK, and checks every
VCF/gVCF body field as well as the expected three heterozygous calls.

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
