# Native Windows tools

Bioconda builds its tools for Linux and macOS only. On Windows, Pixi installs
WGS Extract's own builds of them instead, made from these recipes:

| Package | Executables |
| --- | --- |
| htslib | bgzip, tabix, htsfile |
| samtools | samtools |
| bcftools | bcftools (no plugins) |
| bwa | bwa |
| bwa-mem2 | bwa-mem2 (picks the build for the CPU: SSE4.1 to AVX-512) |
| minimap2 | minimap2 |
| fastp | fastp |
| samblaster | samblaster |
| gkl (GATK environment only) | JNI DLLs and a resource-only sidecar jar |

These default packages use native Windows executables built with MSYS2's UCRT64 GCC and linked
statically, so it needs only Windows' own DLLs: no MSYS2, Cygwin or WSL on the
user's PC, and no Linux VM between the tools and the files. Each is a conda
package (`Library\bin\*.exe`) that `pixi.toml` pins by URL and SHA-256.

## Porting

Unix tools assume what Windows doesn't give them. The recipes deal with:

- **32-bit `long`.** Windows is LLP64: `long` is 32 bits, so file offsets
  (`ftell`, `fseek`), counters and bit masks that use it overflow. A human BWA
  index (`.bwt`, 3 GB) made `bwa mem` fail at once with `[ftell] Invalid
  argument`, bwa-mem2's 32-bit popcount and mask bits kept it seeding forever,
  and fastp's read and base counts overflow on a 30x genome. Patches widen them.
- **Large transfers.** A single C++ stream write of the 6.4 GB human
  bwa-mem2 `.0123` index was truncated by exactly 4 GiB on Windows (#264).
  The recipe now chunks both C stdio and C++ stream transfers at 1 GiB,
  checks stream failures, and uses explicit 64-bit worker/length types.
  Its build tests real sparse-file offsets above 4 GiB, partial reads,
  chunk boundaries, and every tiny index file against Linux SHA-256 goldens.
- **Binary I/O.** Windows' C runtime rewrites `\n` as `\r\n` in text mode, and
  UCRT's `binmode.o` is empty. `compat/wgse_compat.c` makes all I/O binary
  before `main()`, so output matches Linux byte for byte.
- **Missing POSIX functions.** `compat/` supplies the drand48 family (POSIX's
  exact generator, so BWA's random choices match Linux), `getrusage`, `times`,
  anonymous `mmap`, `pread`/`pwrite` and `getline`. The header is
  force-included (`-include wgse_compat.h`) and the object linked into each tool.
- **No `fork`/`exec`.** bwa and bwa-mem2 lose their `< command` input pipes and
  `bwa shm`; bwa-mem2's launcher starts its CPU-specific build as a child
  process and waits for it, since Windows' `execv` returns immediately.

Each tool's output was compared with the Linux Bioconda build on the same
input, using real reads against the full hs38DH reference: bwa, minimap2,
samblaster, fastp, and the `align`, `vcf snp`, `bam unalign` and `microarray`
workflows give identical results. bwa-mem2 alignment equivalence was verified
with the chr20 reference. Its `windows-tools-8` package (`2.3-wgse_2`) also
passed the separate native Windows/Linux full-human index gate: all five
files' sizes and SHA-256 hashes match for the 3,217,346,917-base GRCh38
reference, including the 6,434,693,834-byte `.0123` file. See
[#264](https://github.com/WGSExtract/wgsextract-cli/issues/264) for the reference
and complete hash manifest. Tiny tests alone do not establish full-human parity.
Complete verified Linux-built indexes remain usable.

The native BWA build also batches independent suffix-array lookups to overlap
memory reads through the large human index. It retains the original seed
selection and chaining order, reusing one scratch vector per worker. Its native
test compares every position with the scalar lookup across sampling intervals,
input orders and batch boundaries. The full-reference alignment benchmarks use
the same thread and sort-memory settings and verify identical decoded output.

The BWA recipe also reuses `native/bwa`'s exact seed-extension kernel. It runs
eight 16-bit cells at once using SSE2, retaining upstream's implementation for
unsupported scoring ranges. Windows PE/COFF does not use the Linux ELF build's
AVX2 IFUNC resolver; the existing x86-64-v2 requirement is unchanged. The recipe
compares every output field with upstream across 200,000 randomized extensions.

samblaster has a stream-owned 64-KiB reader that locates newlines in bulk,
instead of calling the UCRT reader for every byte. It preserves record order,
long records and final unterminated lines. File and pipe tests compare it with
the original reader, including an exact buffer-ending newline. The general
POSIX `getline` shim is unchanged.

The HTS builds (htslib, samtools and bcftools) reduce CRAM decoding overhead by
copying stop-terminated byte arrays in bounded chunks and packing common BAM
bases with SSSE3. Unusual bases retain the scalar conversion. A thread-safe
cache reuses malloc-compatible CRAM buffers instead of repeatedly returning
their pages to Windows. It retains at most 16 MiB across 16 buffers, accepts
buffers from 64 KiB to 8 MiB, and limits excess capacity to 12.5% when reusing
one. Each build runs codec and sequence equivalence tests plus concurrent
compressed-block, allocation-boundary and error-recovery checks.

bcftools also copies a single input's pileup in bulk when there is exactly one
output sample. Multiple inputs or samples keep the original grouping path.
Its build compares both paths with the upstream implementation across growth
boundaries, empty/repeated calls, and read-group sample selection.

HTS tools also use link-time optimization and inline memory operations. These
flags retain the existing x86-64-v2 CPU requirement and floating-point rules;
other tool recipes keep their own compiler settings.

bcftools additionally links mimalloc 3.6.1 statically. Link-time wrappers cover
both direct and dllimport allocation calls, including `_msize` capacity queries
used by the CRAM buffer cache. UCRT-owned pointers, such as those returned from
inside `_strdup`, retain their original free/realloc/size-query path. No UCRT
DLL is patched and no allocator DLL is installed or injected. The build tests
concurrent ownership transfers, real CRAM codecs and single/multisample and
empty-input variant calls against an otherwise identical UCRT build.
samtools additionally ships `samtools-mimalloc.exe`, using the same guarded
static bridge. Its system `samtools.exe` remains unchanged. The build compares
exact decoded CRAM 3.1 records and verifies allocator activation in only the
candidate. `bam to-cram --cram-version 3.1` with a local reference selects the
matching validated variant under `mimalloc = "auto"`; `--mimalloc off` uses the
system build. The CLI never installs or downloads a variant at runtime.
Missing, broken or version-mismatched variants retain the system allocator.
The manifest records `samtools_allocator`. Other samtools workloads and the
compatibility-default CRAM 3.0 are unchanged; only standalone CRAM 3.1 encoding
passed the [Windows measurement gate](https://github.com/WGSExtract/wgsextract-cli/issues/1123)
and whole-genome confirmation. Apple Silicon bcftools injection is unchanged.

**Release status:** `windows-tools-9` contains the shared extension kernel,
buffered SAM reader and bcftools allocator. The separately pinned
`windows-tools-10` samtools package adds the qualified CRAM allocator variant;
other package pins remain unchanged.
See [the Windows performance track](https://github.com/WGSExtract/wgsextract-cli/issues/673)
for the matched-machine benchmarks and record hashes.

## Building

### wgse-bwa (Windows x86-64)

`wgse-bwa` is built from a separate recipe. The default win-64 Pixi environment
pins the package from `windows-tools-11` without changing the other tool pins.
It uses the MSVC ABI: clang-cl compiles BWA's GNU-style C
extensions, nvcc uses Visual Studio 2022 `cl.exe` for its host code, and
lld-link links the executable. It does not use MSYS2. CUDA 13.5 and the
static CUDA runtime are build-time conda-forge packages, installed in the
isolated build environment rather than system-wide on the runner. The
Windows build includes native SM 120 code for desktop Blackwell, retains
Turing through Hopper machine code, and ships compute 90 PTX for other
newer GPUs. The Linux recipe continues to use CUDA 12.9 and its existing targets.

Install Git for Windows (for Bash during tests) and Visual Studio 2022
Build Tools with the x64 C++ tools and Windows SDK, then build from the root:

```powershell
pixi exec --spec rattler-build==0.77.0 rattler-build build --no-build-id `
    --recipe packaging/windows/recipes/wgse-bwa --channel conda-forge `
    --output-dir out/conda --target-platform win-64
```

The build tests CPU alignment with CUDA disabled, then packages the release
executable and checks its capabilities. The package installs and runs on the
CPU without an NVIDIA driver. Only the optional CUDA backend requires a
supported NVIDIA GPU and driver; no CUDA Toolkit installation is needed.
[Windows alignment](../../docs/windows.md#wgse-bwa-and-optional-nvidia-cuda)
documents how to opt in to CUDA or override the packaged build.

### GATK kernels

GKL is a separate Windows-only dependency of the `gatk` feature. Its
`Library/share/gkl/gkl-native-wgse.jar` adds statically linked GKL 0.8.11 utils
and serial/OpenMP AVX PairHMM plus AVX2 Smith-Waterman DLLs to the unchanged
GATK 4.6.2.0 jar. OpenMP workers enable FTZ/DAZ and restore their prior MXCSR.
The CLI rejects an older sidecar missing the new kernels and gives explicit
installer/Pixi repair instructions. The CLI invokes
the environment's Java 17 with an explicit classpath and the upstream
launcher's JVM options; `java -jar` would ignore the sidecar.
Native compression is still deferred. The caller warns about Java compression
and potential numerical variation. The dedicated hosted Windows CI gate requires
actual utils/AVX OpenMP PairHMM/AVX2 SW loading, a correct tiny-region genotype,
and the matched-ISA JNI parity probe, never Java kernel fallback.
Linux/macOS GATK invocation is unchanged.

The recipe gate above builds and tests a new package. The separate
[GATK native Windows smoke](../../.github/workflows/gatk_windows.yml) job runs
on pull requests and main pushes using the **locked, released package**. It
requires native kernels and complete tiny-VCF body parity against the stock
Linux AVX2 oracle in `tests/fixtures/gatk-linux-avx2/` (source/version/ISA/hash
provenance included), plus the native JNI parity probe. Its input is generated
locally: a 3-kb reference and 380 reads, with no genome downloads or native
compilation. Logs, decoded records and numeric/unit diagnostics are uploaded
even on failure. Neither gate waives the matched-ISA full-chr20 release check.

GKL pins belong in `[feature.gatk.target.win-64.dependencies]`, not the default
Windows-tools block. The GKL source license is MIT with BSD-3-Clause exceptions. Static GCC runtimes
use GPL-3.0-or-later with the GCC runtime exception; the sidecar and conda
package preserve their notices. MSYS2's GCC16 makes `libgomp` a separate
optional build package, explicitly listed in the build toolchain.

### Tool release

The [Windows Tools](../../.github/workflows/windows_tools.yml) workflow builds
every recipe on pull requests that change them. To release a complete set, run
it with a new `release_tag` (`windows-tools-<n>`), then pin it:

```sh
python packaging/windows/pin.py windows-tools-<n>
pixi lock
```

**Partial default-tool releases:** never use `pin.py`, which replaces that entire pin block
with the release's assets and would remove the other tools. Update only the
selected URL/SHA-256 entries by hand, then run `pixi lock`, preserving all
other pins. `windows-tools-5` contains only htslib, samtools and bcftools;
the other five tools remain pinned to their prior releases.

To build the MSYS2 recipes on a Windows PC, install [MSYS2](https://www.msys2.org) and the
packages in [msys2-packages.txt](msys2-packages.txt) (from an MSYS2 shell:
`grep -v '^#' msys2-packages.txt | xargs pacman -S --needed`), then from the
repository root:

```powershell
$env:MSYS2_ROOT = "C:\msys64"   # where MSYS2 is
$env:PATH = "$env:MSYS2_ROOT\ucrt64\bin;$env:MSYS2_ROOT\usr\bin;$env:PATH"
$recipes = Get-ChildItem packaging/windows/recipes -Directory |
    Where-Object { $_.Name -ne 'wgse-bwa' } |
    ForEach-Object { '--recipe'; $_.FullName }
pixi exec --spec rattler-build==0.77.0 rattler-build build `
    @recipes --output-dir out/conda --target-platform win-64
```

An MSYS2 recipe is `recipe.yaml` (source URL and SHA-256, patches, tests)
plus `build.sh`, which sources [recipes/env.sh](recipes/env.sh) for the shared
flags. `wgse-bwa` instead uses `build.cmd`. To change a patch, unpack the source
into a git repository, apply the patch, edit, and regenerate it with `git diff`
(some upstream files are CRLF; `.gitattributes` keeps patches byte-exact).
