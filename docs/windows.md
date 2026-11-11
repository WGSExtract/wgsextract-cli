# Windows

WGS Extract CLI runs natively on Windows the same way it runs on macOS and
Linux: Pixi installs Python, the CLI and the external tools into the project's
environment, and the CLI runs every tool from there. There is no WSL, MSYS2 or
other Unix layer to set up, and tools read and write your Windows files
directly.

## Installing

Run `install_windows.bat` from PowerShell or a command prompt, in the folder
you downloaded the CLI to (or alone, in an empty folder: it then downloads the
latest release there first). It installs Pixi for your user if it is missing,
installs the Pixi environment and checks the tools with `wgsextract deps check`.

If Pixi was installed for the first time, close and reopen PowerShell or the
command prompt before running `pixi`, so the new window picks up the updated
user PATH.

```bat
.\install_windows.bat
pixi run wgsextract --help
```

The folder can be anywhere: another drive (`D:\WGSE`), or a path with spaces,
parentheses or `&` in it. Pixi goes in `%USERPROFILE%\.pixi`, or under
`PIXI_HOME` if you have set Pixi's own `PIXI_HOME`; the installers and the CLI
look for it there even when it is not on PATH.

`uninstall_windows.bat` removes the environments and clears old runtime
settings from `config.toml`; `--remove-prerequisites` also removes Pixi.
Installers before this one set up MSYS2 (by default at `C:\msys64`) for their
tools. Nothing uses it now; `uninstall_windows.bat --remove-msys2` removes it if
you have no other use for it.

## Where the tools come from

Bioconda publishes no Windows packages, so WGS Extract builds its own:
samtools, bcftools and htslib's bgzip, tabix and htsfile, BWA, bwa-mem2,
minimap2, fastp and samblaster are compiled from their upstream releases with
MinGW-w64 (UCRT), linked statically so each `.exe` needs only Windows' own DLLs,
and packaged as conda packages that `pixi.toml` pins by URL and SHA-256. The
recipes, patches and build instructions are in
[packaging/windows/README.md](../packaging/windows/README.md).

Java tools (GATK, FastQC, Haplogrep 3, haplocheck) come from conda-forge,
Bioconda's platform-independent packages and PyPI as on the other platforms.
Y-DNA analysis is native Yleaf-derived Python code; its checksum-pinned tables
download on demand into the configured reference library, not the app bundle.
The GATK environment also includes the native Windows GKL sidecar described
below.
haplocheck 1.3.3 can't load its HTML report template on Windows, so
`lineage mt-haplogroup --contamination` writes only its text reports there
and warns `haplocheck_no_html`.
`deps check` lists what isn't available on Windows (for example Ensembl VEP,
DeepVariant, Delly and FreeBayes).

Variant effect prediction runs on Windows with vep-rs, the default VEP
engine everywhere: install its environment
(`pixi install --locked -e vep-rs`), build its cache with
`wgsextract vep download`, and annotate with `wgsextract vep run`. Ensembl
VEP (`--engine ensembl`) isn't available on Windows. See
[VEP engines](vep-engines.md).

Local paths passed to Java tools use native Windows separators, including
references, inputs, outputs, interval files and temporary directories. You can
still enter paths such as `R:/data/sample.bam`; the CLI converts them before
launching Java so GATK does not mistake `R:` for a URI provider. Explicit URIs
are preserved.

Commands needing pyBigWig (bigWig-backed `vcf phylop`, `vcf gerp` and their
reference downloads), Ensembl VEP (`--engine ensembl`; the default vep-rs works), DeepVariant, Delly,
FreeBayes, Cyrius, T1K, ExpansionHunter, VerifyBamID2 (`qc contamination`),
somalier (`qc relate`) or PacBio tools are not supported on Windows. They fail
before processing inputs or creating output folders, naming the unavailable
tool. PhyloP TSV/VCF resources remain supported. Parabricks `--gpu` modes
require Linux with an NVIDIA GPU.

## PharmCAT on native Windows

`vcf pharmcat` runs natively. The `pharmcat` Pixi environment supplies Python,
conda-forge's Java 17 and the same Windows Tools bcftools/htslib builds as the
default environment; PharmCAT itself is its pipeline release, extracted as on
the other platforms (see `vcf pharmcat`). The CLI runs the pipeline's Python
script and PharmCAT's `java -jar` through that environment, with no shell
wrappers, so paths with spaces work. Install the runtime and check it with:

```bat
pixi install --locked -e pharmcat
pixi run wgsextract deps check --tool pharmcat_python
```

`typing cyp2d6` and `typing hla` don't run on Windows, so without
`--outside-call` files made elsewhere the run logs "CYP2D6 not called" and the
report has no CYP2D6 or HLA-A/HLA-B calls. The
[PharmCAT parity CI](../.github/workflows/pharmcat_e2e.yml) checks that a GIAB
HG002 VCF gives the same `phenotype.json` on Windows and Linux, except for its
timestamp.

## GATK on native Windows

Standard-read HaplotypeCaller uses native **OpenMP AVX PairHMM and AVX2
Smith-Waterman** on AVX2-capable x64 Windows CPUs. The URL/SHA-pinned GKL 0.8.11
sidecar supplies four static DLLs alongside unmodified GATK 4.6.2.0 and Java 17.
No WSL, MSYS2 installation, extra OpenMP runtime or manual Java classpath is
needed.

`install_windows.bat` prepares both the default and GATK environments.
For a manual setup, install the locked GATK environment explicitly before
calling; the CLI checks presence and does not install it for you:

```bat
pixi install --locked -e gatk
pixi run wgsextract vcf gatk --input "C:\genomes\sample.bam" --ref "C:\refs\reference.fa" --region chr20 --threads 4 --memory 512M --outdir "C:\results\gatk"
```

The log should include:

```text
Intel GKL Utils loaded
Using the OpenMP multi-threaded AVX-accelerated native PairHMM implementation
Using AVX accelerated SmithWaterman implementation
```

If the sidecar or pinned GATK jar is missing or obsolete, **re-run
`install_windows.bat` or `pixi install --locked -e gatk`**. A missing native-load
message, Java Smith-Waterman, or `LOGLESS_CACHING` fallback means the accelerated
path is not in use. Check CPU support and the preceding GKL load diagnostics;
fallback results can differ numerically from Linux.

The [matched-ISA public HG00096 chr20 validation](https://github.com/WGSExtract/wgsextract-cli/pull/228)
produced **127,210 records byte-identical across all eight calls**, including
every numeric, depth, genotype and FILTER field. With one warmup and three
measured repetitions on matched 4-vCPU/16-GiB AMD EPYC 7763 VMs, medians were
1420.883 s on Windows and 1450.530 s on Linux (**0.97956×**).
This is a specific standard-read/AVX2 result, not a guarantee for every dataset
or CPU: AVX-512 is not enabled in the Windows sidecar, VCF headers and compressed
bytes are not parity targets, and the CLI retains its numerical/performance
warning. Compression and Ultima's flow-likelihood engine still use Java.

The [GATK native Windows smoke CI](../.github/workflows/gatk_windows.yml)
consumes the installed, locked sidecar without rebuilding it or downloading
genome data. It checks native loading, all fields of a tiny synthetic call
against the stock Linux AVX2 golden, and native PairHMM/Smith-Waterman unit
parity. Java fallback or changed VCF fields fail the gate; this does not replace
the full-chr20 release gate.

The CLI launches Java with the sidecar/GATK classpath. Launcher `--java-options`
remain JVM options, including scatter workers' heap and CPU limits; Linux/macOS
launching and default caller arguments are unchanged.

## Cancelling commands

Windows cleanup terminates the registered launcher and its descendants together,
including Java workers. It does not use Ctrl+Break, which Java can ignore.
Native GATK single-shard subprocess and stderr waits check back with Python every
half second, so foreground keyboard Ctrl+C does not wait for Java to finish before
the CLI's signal handler can terminate the process tree. The half-second interval
is not a guarantee on total cleanup time; Windows must also stop the descendants.

## Short read alignment

The default `--aligner auto` uses native bwa-mem2 when the reference already
has its index and the index fits in memory. Otherwise it uses BWA. To prepare
the bwa-mem2 index for your reference, run:

```bat
pixi run wgsextract ref index --aligner bwa-mem2 --ref C:\path\to\reference.fa
```

Native full-human index construction is supported with the pinned
`windows-tools-8` bwa-mem2 `2.3-wgse_2` package. It fixes the large-transfer
and 64-bit-offset corruption tracked in [#264](https://github.com/WGSExtract/wgsextract-cli/issues/264).
All five index files for the 3,217,346,917-base GRCh38 reference were verified
byte-identical to a Linux-built index. Building it needs roughly 80 GB of RAM;
the CLI checks its build-memory estimate before starting. Existing indexes and
the verified published hs38DH index download remain supported.
`ref index`, alignment and realignment prepare missing FASTA length metadata
automatically with samtools. Direct build
preflight refuses missing or incomplete length metadata rather than assuming
the build needs no memory; complete BWA `.ann` metadata can also supply the length.

The human bwa-mem2 index occupies about 17 GB of RAM, with additional memory
needed for reads, sorting and Windows. The CLI checks the reference's memory
estimate before choosing it. `--aligner bwa` selects the lower-memory option;
`--aligner bwa-mem2` selects bwa-mem2 explicitly.

## Upgrading from a WSL or MSYS2 setup

Older Windows installs used a `tool_runtime` setting (`windows`, `pacman`,
`wsl`, `cygwin` or `msys2`) and `pacman_ucrt64_bin`. The CLI now ignores these
settings, and drops them from `config.toml` the next time it saves it. Rerun
`install_windows.bat` to get the native tools.

## Release test on Windows 11 (v0.8.5)

v0.8.5 was tested end to end on a fresh Azure Windows 11 Pro 25H2 VM
(build 26200, Standard_D16ads_v5: 16 vCPUs, 64 GiB RAM, local NVMe `D:`) in
[#554](https://github.com/WGSExtract/wgsextract-cli/issues/554), as a standard
user with no tools preinstalled.

- **CLI install.** The release's source zip and `install_windows.bat` took 123 s
  and exited 0.
- **CLI smoke test.** 49 steps covering every command family on the small
  `hg19-mini` HG00096 data: `info`, `extract`, `microarray`, `lineage`, `bam`,
  `qc`, `align`, `realign`, `vcf snp`/`indel`/`gatk`/`annotate`/`filter`/`table`,
  `report` and `report-issue`. All behaved as expected, including the documented
  refusals of DeepVariant, FreeBayes and VEP on native Windows. Only
  `lineage y-haplogroup` and `lineage y-str` failed, as they do on Linux: the
  mini reference's chrY is a 500 kb slice renumbered from position 1, so the
  hg19 positions these tools look up don't line up with it.
- **Server mode.** `wgsextract serve` answered `/v1/health`, refused requests
  without the token (401) and paths outside `--allow` (403), and ran an `info`
  job to completion.
- **Desktop app.** The Install Lab installed the v0.8.5 app, ran first-run setup
  by itself (87 s) and passed all 15 planned actions on the mini genome: mtDNA
  haplogroup H16a1 as published, 88.3% recall of the published SNPs in its 1 Mb
  chr20 slice, 93.6% GATK precision in the first 200 kb of that slice and 99.81%
  of reads mapped by `align`. The *Report a problem* preview was redacted.
  Uninstalling asked whether to delete the workspace: answering Yes removed it,
  answering No kept it, and both removed the runtime and app data. The installer
  is unsigned, as documented. Each run passed 24 of 26 checks. In both runs one
  failure was pixi, already installed by the CLI test. The other was the
  uninstall check's list of files left behind: in the Yes run, Store-app folders
  that come with the Azure image; in the No run, the workspace it kept on
  purpose, plus App Installer and Credential Manager files that Windows wrote
  (the app has no code that uses Credential Manager).
- **Auto-update.** The v0.6.6 app found v0.8.5 through the bridge release in
  `gui-for-cli-outdated`, verified its signature, installed it and relaunched as
  0.8.5 within about 30 s. A silent uninstall (`/S`) kept the workspace.
- **30x genome.** HG002 NovaSeq PCR-free 30x GRCh38 BAM (GIAB), realigned to
  `hs38` with the default settings (16 threads):

  | step | time |
  | --- | --- |
  | download the 30x BAM (curl) | 5 min |
  | `ref library --install hs38` | 44 s |
  | `realign` (builds the BWA index first: none ships for hs38) | 8 h 31 min |
  | `info` on the realigned BAM | 2 s |
  | `vcf snp` | 2 h 48 min |
  | `vcf indel` | 3 h 3 min |

  `realign` used `bwa`, because there is no bwa-mem2 index for hs38. Unpacking
  the reads took 23 min and building the BWA index 58 min; aligning and sorting
  took the remaining 7 h 10 min. 99.70% of the reads mapped and 97.76% were
  properly paired.

  Memory peaked at 35.8 GiB in use (the preflight estimate was 40.0 GiB), with
  `samtools` the largest process at 23.2 GiB. `realign` used at most 157 GiB of
  disk on top of its 40.7 GiB input BAM; its preflight, which leaves out
  existing inputs, had estimated 210 GiB. The realigned BAM is 37.2 GiB.
  The calling steps used under 6 GiB of memory.

  `vcf snp` called 4.41 million SNPs (3.91 million with QUAL ≥ 30 and depth ≥ 10,
  Ti/Tv 1.99) and `vcf indel` 0.92 million indels (0.84 million with
  QUAL ≥ 30 and depth ≥ 10).

  v0.8.5 called `vcf snp` and `vcf indel` in a single bcftools stream on one
  core. After v0.8.5,
  [#560](https://github.com/WGSExtract/wgsextract-cli/pull/560) made them call
  contigs in parallel by default when `--threads` is above 1. The "Expected
  Wait" warning said 21 minutes for each
  ([#583](https://github.com/WGSExtract/wgsextract-cli/issues/583), fixed on
  `main` by [#586](https://github.com/WGSExtract/wgsextract-cli/pull/586)).

Bugs found, fixed on `main` after v0.8.5:

- [#558](https://github.com/WGSExtract/wgsextract-cli/pull/558): a
  `config.toml` saved with a UTF-8 byte-order mark (as Windows PowerShell 5's
  `Set-Content -Encoding utf8` writes it) was rejected as invalid TOML.
- [#568](https://github.com/WGSExtract/wgsextract-cli/pull/568): `serve` printed
  `ConnectionResetError` tracebacks when a client dropped a keep-alive
  connection.
- [#571](https://github.com/WGSExtract/wgsextract-cli/pull/571): the
  `report-issue` preview redacted generic file names such as `result` and tool
  names such as `gatk` as if they were sample names.
- [#572](https://github.com/WGSExtract/wgsextract-cli/pull/572): the Install Lab
  did not find the uninstaller's workspace question on Windows 11 25H2, which
  shows it as a child window of the wizard.
- [#586](https://github.com/WGSExtract/wgsextract-cli/pull/586): the `vcf snp`
  and `vcf indel` "Expected Wait" was divided by the thread count even when
  calling ran in one stream.

# Windows ARM64

Native Windows ARM64 (`win-arm64`) is being qualified separately in
[`packaging/windows-arm64`](../packaging/windows-arm64/README.md). It is not yet
enabled in the locked installer. The installer refuses ARM64 before downloads
or environment creation, including when invoked from an x64 terminal under
Prism. Do not substitute x64 emulation, ARM64EC, WSL or x86 containers.
GATK/GKL and DeepVariant are unavailable on this architecture in phase 1;
they fail before caller execution instead of silently choosing Java kernels.
