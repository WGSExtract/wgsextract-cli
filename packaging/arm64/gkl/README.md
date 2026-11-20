# Native Arm64 GKL sidecar (prototype, issue #236)

GATK 4.6.2 only accelerates HaplotypeCaller when GKL reports AVX. On Arm64 it
otherwise falls back to the Java PairHMM and Smith-Waterman. This recipe ports
the pinned GKL 0.8.11 AVX PairHMM and AVX2 Smith-Waterman kernels to AArch64.
The hot loops are native NEON with the same per-cell IEEE operation order, so
the results are bit-identical to x64. The pinned header-only SIMDe v0.8.2
supplies the remaining AVX scaffolding. The recipe also adds a loader overlay
that reports Arm support truthfully.

- `patches/0002-aarch64-neon-simde.patch` applies on top of the Windows
  `0001-windows-avx-native.patch`. It adds:
  - `common/simd_compat.h`: the SIMDe include, `_mm_malloc` and FPCR-backed
    `_MM_SET_FLUSH_ZERO_MODE`;
  - `pairhmm/neon-pairhmm.h`: the native NEON float/double PairHMM, used by
    `avx_impl.cc` (the float→double fallback is unchanged);
  - a native NEON `VEC_*` macro set in `smithwaterman/avx2-functions.h` for the
    unchanged `PairWiseSW.h`;
  - an FPCR branch in `ScopedFtzDaz`;
  - `is_neon_supported()` plus the JNI `isNeonSupportedNative`;
  - NEON gates in `IntelPairHmm`/`IntelSmithWaterman`;
  - `native/linux-aarch64/` and `native/windows-aarch64/` resource directories in
    `NativeLibraryLoader`.

  x86 behaviour and the x86 resource path (`native/`) are unchanged.
- `build-linux-aarch64.sh WORK GATK_JAR OUT` does the following:
  - fetches the GKL and SIMDe sources and verifies their SHA-256;
  - applies both patches;
  - builds `libgkl_{utils,pairhmm,pairhmm_omp,smithwaterman}.so` with
    `-march=armv8-a -ffp-contract=off`, static libstdc++/libgcc and dynamic
    glibc/libgomp;
  - rejects unexpected dynamic dependencies and any fused multiply-add instruction,
    using the fail-closed guard in `fma_guard.sh` after its self-test proves it
    rejects an FMA-bearing sample;
  - runs the OpenMP FPCR test;
  - writes a reproducible `gkl-native-wgse-linux-aarch64.jar` (patched classes,
    `.so` files and licences).
- `build-windows-arm64.sh WORK GATK_JAR OUT` is the win-arm64 counterpart. Run it
  in an MSYS2 CLANGARM64 shell on Windows 11 Arm64 with `JAVA_HOME` set to an Arm64
  JDK 17. It does the following:
  - builds native ARM64 (`0xAA64`) `gkl_{utils,pairhmm,pairhmm_omp,smithwaterman}.dll`
    with clang and the same flags and no-FMA guard;
  - links the LLVM runtimes statically, but ships `libomp.dll` shared, because
    two static OpenMP runtimes in one JVM abort;
  - checks each DLL's machine type and imports;
  - writes `gkl-native-wgse-windows-aarch64.jar`.

  On this target `common/wgse_libm.h` replaces the UCRT `log10/log10f/pow/log1p`
  with glibc-exact ports, so results match Linux bit for bit
  (`licenses/wgse_libm/NOTICE.txt`).
- Launch: `java -cp gkl-native-wgse-linux-aarch64.jar:gatk-package-4.6.2.0-local.jar org.broadinstitute.hellbender.Main HaplotypeCaller ...`.
  The sidecar classes shadow GATK's bundled GKL classes. With the stock GATK
  jar alone, Arm64 keeps the Java fallback. Compression stays on the JDK
  deflater/inflater. On Windows, use the `windows-aarch64` jar and `;` as the
  classpath separator.

Tests (`tests/`):

- `fp_semantics.cc`: reports control-register flush/denormal/tininess behaviour and
  hashes of the PairHMM tables exactly as `Context<float>`/`Context<double>` initialize
  them (`context_*`), plus fingerprints of the libm calls they use (`libm_*`). Both must
  match across platforms; on Windows the raw UCRT is also reported (`crt_*`).
- `omp_fpcr_state.cc`: on a cold OpenMP pool, runs the production FZ arrangement and
  then asserts in an unguarded region that every pool thread is back to its original
  FPCR. It also checks per-worker FZ and full FPCR restoration. `legacy` mode runs the
  pre-fix caller guard; the Linux build requires it to leak (exit 2).
- `GklVectorProbe.java`: native float/double and Java `LoglessPairHMM` bits over
  GKL test vectors and seeded random batches, plus all 14,196 GKL Smith-Waterman
  test pairs × 4 overhang strategies.
- `kbench.cc` / `kbench.sh GKL_NATIVE_DIR SIMDE_DIR OUT`: kernel-only timing
  and exact-bit hashes over 20,000 seeded PairHMM cases and the GKL SW pairs.
  x86-64 and AArch64 must print the same `hash_*` values. A missing, empty, odd-length
  or blank-line SW input exits non-zero instead of printing the initial hash, and
  `kbench.sh` self-tests that before the real run.
- `compare_vectors.py`: compares the Arm64 probe output against the x64 output.
- `JavaThreadFtzProbe.java THREADS`: asserts that the FP state of the calling thread
  and of a newly created thread is clean (subnormals survive, FTZ off) after
  `initialize`, compute and `done()`, for float and double. `1` tests `IntelPairHmm`
  and `4` tests `IntelPairHmmOMP`; run each in its own JVM. It exits 1 on Arm64 if
  FP state leaks; on x86 the stock leak is reported only.
- `packaging/windows/recipes/gkl/tests/GklParityProbe.java` accepts a third argument,
  `neon`, so that it can qualify this sidecar against `linux-avx2-golden.tsv`.

See `DESIGN.md` for the design rationale; the prototype results are in
`out/arm64/gkl/prototype-results.md` of the issue #236 work.

## Apple Silicon PairHMM (experimental, issue #1117)

`build-darwin-aarch64.sh WORK GATK_JAR OUT [--download]` builds the **same
NEON PairHMM**, not another kernel. It applies the existing Windows/ARM
patches plus a macOS resource-directory entry in the existing loader.
The GKL JNI methods and `META-INF/wgse-pairhmm.json` schema are shared with
[#1134 (Linux CUDA PairHMM)](https://github.com/WGSExtract/wgsextract-cli/pull/1134).
GKL 0.8.11 and SIMDe 0.8.2 are MIT-licensed (not Apache-2.0); their licences
and this repository's GPLv3 licence for WGSE changes are included.
Smith-Waterman and compression remain Java.

Install prerequisites yourself: Apple Silicon macOS, Xcode Command Line
Tools, JDK 17+ (`java`, `javac`, `jar` on PATH), Python 3, curl, and Homebrew
`libomp` and `llvm`. The recipe validates these tools; it does not install
them. Missing pinned public source archives error unless **`--download`**
explicitly confirms fetching GKL/SIMDe from GitHub. No sample data is sent.

```bash
bash packaging/arm64/gkl/build-darwin-aarch64.sh \
  out/pairhmm/source /path/to/gatk-package-4.6.2.0-local.jar out/pairhmm/package \
  --download
wgsextract vcf gatk --input sample.bam --ref reference.fa \
  --pair-hmm-neon out/pairhmm/package/gkl-native-wgse-darwin-aarch64.jar \
  --threads 4 --reference-evidence gvcf --outdir out/calls
```

Opt-in only: normal commands are unchanged. The jar records the absolute
build-time JDK and GATK paths; rebuild it if those installations move. The
CLI validates those local inputs and launches that JDK with the existing
ARM loader overlay before GATK's bundled GKL. This is necessary because
stock GKL rejects non-AVX hosts; unlike CUDA, ARM cannot use a resource-only
bootstrap append. There is no replacement GATK API or separate JNI binding.
`libomp` remains a local runtime dependency, not a bundled runtime.

The CLI always requests **double precision**: native float passed the
likelihood tolerance and call checks, but changed one reference block's DP
on the expanded HG002 region. Double precision corrects that regression
without changing the existing NEON kernel or the native interface.

With valid build inputs but unavailable/unloadable native libraries, GATK
automatically selects Java `LOGLESS_CACHING`. Explicit incompatible
sidecars, missing build inputs and unsupported platforms fail preflight
instead of silently ignoring the requested input. Do not combine this
option with Parabricks `--gpu`, Ultima flow likelihoods, or `--scatter`.
Verify the `Using Arm NEON PairHMM kernels` log: GATK itself calls this
implementation “AVX-accelerated” even on ARM.

### Qualification

The existing no-FMA disassembly guard runs during the macOS build.
Compile the existing `tests/GklVectorProbe.java` and
`tests/JavaThreadFtzProbe.java` against the GATK jar, then run with
the sidecar before GATK on the classpath:

```bash
GATK_JAR=/path/to/gatk-package-4.6.2.0-local.jar
SIDECAR=out/pairhmm/package/gkl-native-wgse-darwin-aarch64.jar
mkdir -p out/pairhmm/classes out/pairhmm/tmp
javac -cp "$GATK_JAR" -d out/pairhmm/classes \
  packaging/arm64/gkl/tests/{GklVectorProbe,JavaThreadFtzProbe}.java
probe_classpath="out/pairhmm/classes:$SIDECAR:$GATK_JAR"
java -Djava.io.tmpdir=out/pairhmm/tmp -cp "$probe_classpath" JavaThreadFtzProbe 4
java -Xmx2g -Djava.io.tmpdir=out/pairhmm/tmp -cp "$probe_classpath" \
  org.broadinstitute.hellbender.utils.pairhmm.GklVectorProbe \
  out/pairhmm/source/GKL-0.8.11/src/test/resources out/pairhmm/vectors 32000 4 pairhmm-only
bash native/pairhmm/tests/haplotypecaller-neon.sh \
  out/pairhmm/package/gkl-native-wgse-darwin-aarch64.jar out/pairhmm/small
```

Repeat the two Java probes with one native thread. `pairhmm-only` checks
both float-with-double-rescue and explicit double against Java at absolute
log10 tolerance 0.00001 (GKL 0.8.11's unit-test tolerance). The scientific
fixture is shared with the CUDA prototype; its CLI test requires
samtools/bcftools/tabix and checks full
VCF/gVCF bodies for Java, NEON, and a genuinely unloadable-native fallback.
Use `native/pairhmm/tests/compare_calls.py JAVA.g.vcf.gz NEON.g.vcf.gz`
for larger real data: it requires identical ordered sites/alleles/GT/FILTER
and complete reference blocks, while reporting variant annotation drift.

For a matched interval panel, run each backend through the real CLI with
the same input, reference, interval and scientific settings:

```bash
pixi run wgsextract vcf gatk --input HG002.bam --ref hs38.fa \
  --region chr20:17000001-20000000 --threads 4 \
  --reference-evidence gvcf --qc-min-depth 0 --outdir out/pairhmm/java
pixi run wgsextract vcf gatk --input HG002.bam --ref hs38.fa \
  --region chr20:17000001-20000000 --threads 4 \
  --reference-evidence gvcf --qc-min-depth 0 --outdir out/pairhmm/neon \
  --pair-hmm-neon out/pairhmm/package/gkl-native-wgse-darwin-aarch64.jar
pixi run python native/pairhmm/tests/compare_calls.py \
  out/pairhmm/java/gatk.g.vcf.gz out/pairhmm/neon/gatk.g.vcf.gz
pixi run python native/pairhmm/tests/compare_calls.py \
  out/pairhmm/java/gatk.vcf.gz out/pairhmm/neon/gatk.vcf.gz
```

The canonical comparison alone allows arbitrary variant annotations to differ.
Also compare complete non-header lines: any difference must be characterized,
not automatically accepted because sites and genotypes match. The observed
10 Mb double-precision exception changes only PL entries involving `<NON_REF>`
in variant gVCF rows; those alleles are removed by final genotyping.
Reference blocks must still match line for line.
The subsequent five-window difficult-region panel **fails** this requirement
despite double precision: nine reference blocks and eight final-VCF annotation
rows differ. Do not treat a native likelihood tolerance test, unchanged GT,
or successful software certification as acceptance of those differences.

Measure wall, CPU and largest-process RSS around the complete CLI (including
GenotypeGVCFs), and report the logged PairHMM compute timer separately.
Use independent process-group supervision for a hard resource-turn deadline:
the previous nested timeout/caffeinate wrapper failed to bound a slow Java run.
Clipping intervals changes assembly context, so matching a panel does not prove
an unpartitioned chromosome is identical.

This is not whole-genome qualification; see
[measured interval evidence](../../../docs/hardware.md#experimental-apple-silicon-neon-pairhmm).
