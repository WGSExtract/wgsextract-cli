# Tools

The **Tools** page (near the bottom of the sidebar) shows every bioinformatics
tool that WGS Extract uses — both the **mandatory core tools** installed during
initial Setup and the **optional tools** for specialized workflows (such as
variant callers, annotation engines, phasing/imputation, and long-read analysis).

## How tools are managed

WGS Extract bundles and runs its tools through isolated, version-locked
**Pixi** environments managed by the CLI:

- Opening the **Tools** page runs `wgsextract deps list` locally to inspect
  installed versions and platform availability. It does **not** download or
  install anything automatically.
- Because probing every installed tool version takes a moment (especially on
  first launch or on Windows), the table may show a loading indicator briefly.
- Hover over any row to view a provenance tooltip with the tool's purpose,
  upstream homepage, authoring organization, and software license.

## Columns

- **Name:** Tool name (such as `samtools`, `bcftools`, `bwa-mem2`, `minimap2`, `gatk`, `vep-rs`, `clair3`, `modkit`).
- **Details:** Installed version, status, or platform/availability notes.
- **Environment:** Which Pixi environment provides the tool (or whether it is provided externally / by the OS).

## Row actions

The action button on each row reflects what WGS Extract can do on your computer:

| Action | Meaning |
| --- | --- |
| **Install** | Runs `wgsextract deps install <tool>` to install an optional tool: its Pixi environment, or its pinned official release (PLINK 2 on Windows). Streams progress to the terminal pane. |
| **Install** (licence) | For a tool with a restrictive licence (Dorado: the Oxford Nanopore Technologies PLC. Public License Version 2.0, research use only), first shows the licence and its terms. **Accept and install** runs `wgsextract deps install <tool> --accept-license`. |
| **Uninstall** | Asks for confirmation and runs `wgsextract deps uninstall <tool>` to remove an optional environment and free disk space. |
| **Install it yourself** (disabled) | This tool is managed outside Pixi (for example, Docker-based DeepVariant or a separately installed runtime). Hover over the button to read the CLI's install instructions. |
| **Not available** (disabled) | This tool is not supported on your current operating system or CPU architecture (for example, certain Linux-only callers on Windows or x86-64-only models on Apple Silicon). Hover over the button for details. |

## Core tools and Setup

Mandatory tools (`samtools`, `bcftools`, `tabix`, `bgzip`, `bwa`, `minimap2`,
etc.) are installed together when you run **Setup** on the [Settings](settings.md)
page. If a core tool is missing or broken (for example, after moving accounts on
Windows), rerun **Setup** from [Settings](settings.md).

If you run a command that requires an optional tool you have not installed yet,
the command reports the missing tool rather than silently downloading it during
an analysis run. Install it here (or via `wgsextract deps install <tool>`) and
rerun the action.
