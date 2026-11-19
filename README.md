# WGS Extract CLI (`wgsextract-cli`)

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

An independent command-line recreation of
[WGS Extract](https://github.com/WGSExtract/WGSExtract-Dev/).
Inspect BAM/CRAM files, simulate consumer microarray files, extract Y/mtDNA,
call and annotate variants, and manage reference genomes from the terminal.
It wraps samtools, bcftools, bwa/minimap2, GATK, Haplogrep, VEP and other tools,
with native Yleaf-derived Y-DNA analysis.
[Pixi](https://pixi.sh) provides Python and bioinformatics tools on macOS,
Linux and Windows.

- [Documentation site](https://wgsextract.github.io/wgsextract-cli/)
- [All topic guides](docs/README.md)
- [Desktop app](gui/README.md), which bundles this CLI; installers ship with
  each [release](https://github.com/WGSExtract/wgsextract-cli/releases).
  See the [app site](https://wgsextract.github.io/wgsextract-cli/app/).

## Privacy

Default commands stay offline: no telemetry, background update checks or
silent reference downloads. Missing public data requires an explicit
`--download`; sample-derived information must never be sent without a
separate, documented opt-in.
See [network permissions and destinations](docs/privacy.md).

## Install

On macOS or Linux, open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/WGSExtract/wgsextract-cli/main/install.sh | sh
```

The script comes from `main` but installs the latest published release into
`./wgsextract-cli/`, not the development branch. On macOS it checks for Xcode
Command Line Tools. Linux x86_64 and native ARM64 use the same installer;
see [Linux ARM64 support and limitations](docs/linux-arm64.md).

On Windows, clone or download this repository, then run in PowerShell:

```powershell
.\install_windows.bat
```

This installs Pixi when missing and native Windows tools; no WSL or MSYS2 is
needed. See the [Windows guide](docs/windows.md).

[Installer options and uninstall details](docs/installation.md) cover pinned
versions, custom directories, verification and preserved data.
The [site install guide](https://wgsextract.github.io/wgsextract-cli/install.html)
also walks through platform setup and the reference library.

### Running the CLI

| Install type | Command |
| :--- | :--- |
| macOS/Linux installer | `./wgsextract-cli/wgsextract`, or add `wgsextract-cli/` to `PATH` |
| Source checkout or Windows install | `pixi run wgsextract`, from the repository directory |

Examples below write plain `wgsextract`; substitute your launcher.
Optional tools must be [installed explicitly](docs/developer_setup.md#pixi-environments)
before use. Feature commands check dependencies; they never install them.

### Uninstall

```bash
./wgsextract-cli/uninstall.sh --yes
```

This removes the installation, including reference data inside it.
Back up anything you want to keep first. Configuration and user-level Pixi
are kept unless you request `--remove-config` or `--remove-pixi`.
For Windows and custom locations, see the [uninstall guide](docs/installation.md#uninstall).

## Quick start

Use your own BAM/CRAM in place of `sample.cram`. Install the exact reference
it was aligned to: `hs38` below is an example, not interchangeable with every
GRCh38 reference.

```bash
# Explicit downloads: library metadata, then a matching reference genome
wgsextract ref bootstrap
wgsextract ref library --list
wgsextract ref library --install hs38

# Validate installed tools (exit 3 if a mandatory tool is missing)
wgsextract deps check

# Inspect the alignment and its reference build
wgsextract info --input sample.cram --detailed

# Simulate a 23andMe v5 raw-data file
wgsextract microarray --input sample.cram --formats 23andme_v5

# Extract mitochondrial variants and predict the maternal haplogroup
wgsextract extract mito-vcf --input sample.cram
wgsextract lineage mt-haplogroup --input sample.cram
```

Try a small region (`--region chrM`, where supported) before a whole genome:
large jobs can take hours and hundreds of gigabytes of working space.
See [resource estimates](docs/resource_estimates.md) before starting.

Find the next command with `wgsextract help --all` or the
[command reference](docs/commands.md).
For scripts and agents, use `wgsextract help --all --json` for the compact
index, then `wgsextract help <command> --json` for required arguments,
options, examples and typed input schemas. Key flags in the index are hints,
not complete invocations.

Commands exit non-zero on failure. Writers record inputs, outputs, warnings
and provenance in a [result manifest](docs/result-manifest.md); `--json`
prints structured results while logs stay on stderr.
See [running commands](docs/usage.md) for option placement, progress and logging.

## Where to go next

| Task | Guide |
| :--- | :--- |
| Choose a workflow or find a command | [Command reference](docs/commands.md), [workflow recipes](https://wgsextract.github.io/wgsextract-cli/workflows.html) |
| Export consumer chips or AADR panels | [Microarray rules and formats](docs/microarray.md), [AADR](docs/aadr.md) |
| Extract Y/mtDNA or find haplogroups | [Lineage and yBrowse](docs/lineage.md) |
| Call variants, annotate or type pharmacogenes | [Variant workflows](docs/variants.md), [confidence and accuracy](docs/variant-confidence.md) |
| Work with Nanopore or PacBio | [Long-read support](docs/ont.md) |
| Choose hardware and plan disk/RAM | [Hardware and acceleration](docs/hardware.md), [resource estimates](docs/resource_estimates.md) |
| Configure paths, resources or keep-awake | [Configuration](docs/configuration.md) |
| Organise samples or download example genomes | [Dataset library and sample workspace](docs/sample-workspace.md#dataset-library) |
| Install and verify reference genomes | [Reference genomes](docs/reference-genomes.md) |
| Resume an interrupted workflow | [Step journals](docs/step-resume.md) |
| Combine pipeline outputs or MultiQC reports | [Professional pipeline interoperability](docs/interop.md) |
| Read a local personal report | [Personal report](docs/personal-report.md), [genotype QC](docs/genotype-qc.md) |
| Integrate from Python, HTTP or MCP | [Typed Python API](docs/api.md), [local server](docs/server.md) |
| Browse every guide | [Documentation index](docs/README.md) |

## Reporting a problem

Run `wgsextract report-issue --last-failed` to prepare a locally redacted
report, or `wgsextract report-issue --print` to inspect the last run without
opening a browser. Nothing is uploaded automatically: review before submitting.
See [report contents, privacy and options](docs/usage.md#reporting-a-problem),
or [open an issue](https://github.com/WGSExtract/wgsextract-cli/issues/new/choose).

## Development

Start with [developer setup](docs/developer_setup.md) for the setup scripts,
Pixi environments, tests and contribution workflow.
The desktop app has its [own setup guide](gui/docs/developer_setup.md).
See [releasing](docs/releasing.md) for coordinated CLI/app releases.

## Data licences

Downloaded reference data is separate from the program and retains its own
licences. See the [data-module licence table](docs/lineage.md#data-licences)
and [reference sources and terms](docs/reference-genomes.md#sources-and-terms).

Y-DNA prediction credits Yleaf's authors at Erasmus MC: Arwin Ralf, Diego
Montiel Gonzalez, Kaiyin Zhong, Manfred Kayser and Bram van Wersch.
Please cite Ralf et al., *Molecular Biology and Evolution* 35(5):1291–1294
(2018), [doi:10.1093/molbev/msy032](https://doi.org/10.1093/molbev/msy032).
Modified Yleaf-derived files remain GPL-3.0-or-later; see [NOTICE](NOTICE)
and the [upstream sync procedure](src/wgsextract_cli/ydna/yleaf_native/README.md).

## License

Distributed under [GPL-3.0](LICENSE). Downloaded data modules have their own
licences; they do not change the program's licence.
