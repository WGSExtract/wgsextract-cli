# Privacy and network access (Desktop App)

Whole-genome data is deeply personal and uniquely identifying — for you and for
your biological relatives. **All genomic analysis in WGS Extract runs locally on
your computer.** Your FASTQ, BAM, CRAM, VCF, microarray files, genotypes, and
variant coordinates are never uploaded by default, and there is no analytics
tracking or usage telemetry.

This guide explains how the **WGS Extract desktop app** uses the network
and how it differs from the **CLI back-end**. It covers what external servers
can see when you check for updates or download public data, and how to run
completely offline.

For the full command-by-command audit of the CLI engine, see the
[CLI Privacy and network access guide](../../docs/privacy.md). For the
architectural contract between the app and the CLI, see
[Architecture — Privacy and network boundary](../devdocs/architecture.md#privacy-and-network-boundary).

## How the desktop app and CLI work together

The **CLI stays offline by default**. A network request needs an explicit
download or install command, a flag such as `--download`, or an applicable
opt-in in `config.toml`. The desktop app uses those options for you: when you
choose **Install and Continue**, for example, it can request the missing public
data without you typing a command. Lineage and microarray pages also offer
a per-run download option that is off by default. Neither choice turns on
persistent CLI auto-downloads or authorizes sending sample-derived data.

## First launch and Network & Privacy

On a fresh install, the app makes no outbound requests before your first
network choice. **OK** starts setup if it has never run and turns on **Check
for updates at launch**, which is off until you opt in.
**Not now** opens **Settings → Network & Privacy** without contacting an
external server. You can use local features with the tools and data already
installed and choose to run setup or check for updates later.

The app remembers your choice. In **Settings → Network & Privacy**, turn off
**Check for updates at launch** to stop startup checks, or turn on
**Privacy mode** to pause automatic network activity. Privacy mode does not
block explicit actions such as **File → Check for Updates…** or a download
you request. Development builds without an updater key do not check for
updates. If setup was already complete when you upgraded, the app keeps your
existing Privacy mode and carries over whether launch checks were active.

## The four network tiers in the Desktop App

| Tier | Examples | Default | Can it send sample data? | How to control it |
| --- | --- | --- | --- | --- |
| **1. Automatic connections** | First-run setup downloads and app release checks at startup | **Off on a fresh install until you choose OK** | **Never** — downloads public files or sends version metadata, OS/arch, IP, and standard HTTP headers | **Settings → Network & Privacy**; **Privacy mode** pauses automatic connections |
| **2. Interactive prerequisite prompts** | Clicking **Install and Continue** when a command or [Gene Page](gene-page.md) reports a missing reference genome or Gene Map | **Asks before downloading** — never downloads silently | **Never** — fetches public reference files only | Click **Cancel** or **Continue** instead, or pre-install files beforehand |
| **3. Explicit management & help actions** | Running **Setup** manually, clicking **Install / Download** on [Reference Data](reference-data.md), [Reference Genomes](reference-genomes.md), or [Tools](tools.md), clicking **Look up sizes**, opening a documentation link, or clicking **Open GitHub issue** | **Off until you click** | **Never** (except a redacted bug report that you preview and submit yourself) | Only runs when you explicitly click the button or link |
| **4. Sample-derived remote features** | [Remote Genome Viewer](genome-viewer.md#view-files-on-a-remote-server) (**Connect and view remote file**), or realigning a remote `https://` alignment URL | **Always explicit opt-in** (off in Basic & Advanced) | **Yes** — sends requested file paths and genomic byte ranges to the server you chose | Never enabled by default; requires explicit per-action confirmation |

[Open in gene.iobio](gene-page.md#open-in-geneiobio) also requires explicit
per-launch consent. It opens `gene.iobio.io` in your browser with the selected
gene symbol and build only. The site may send that link to its analytics
provider. WGS Extract does not send any sample files; if you choose files on
the site, it may expose them to iobio's servers through `waygate.iobio.io`.

## What external servers can see (IP address & metadata)

Even when **no DNA or sample information** leaves your computer, any internet
request naturally reveals basic network metadata to the host you connect to:

- Your **public IP address** (which can reveal approximate location or ISP)
- **Timing** of the request
- Standard **HTTP headers** (such as user-agent / platform)
- **Which public file, tool, or release** was requested (for example, checking
  whether a newer WGS Extract release exists, or downloading the public `hs38`
  reference genome or `ClinVar` release)

This applies to automatic update checks as well as manual downloads:

- **App update checks** contact the configured GitHub release / updater feed
  (`github.com` / GitHub CDN) to compare your installed app version and
  platform/architecture against the latest release. They never transmit sample
  filenames, file paths, or genomic data.
- **Planned Reference Data, Reference Genomes, and Tools update checks** follow
  the same rule: lightweight read-only metadata checks that report when a newer
  public database (such as ClinVar or yBrowse) or tool release is available,
  without downloading multi-gigabyte files until you explicitly click to update,
  and governed by the same **Privacy mode** switch in **Settings**.
- **Public downloads** contact the upstream publisher (such as GitHub releases,
  Pixi/Conda/PyPI registries, NCBI, UCSC, EBI/Ensembl, Broad/gnomAD, Zenodo,
  or yBrowse) or an audited WGS Extract mirror. See the complete destination
  table in [CLI Privacy — What goes where](../../docs/privacy.md#what-goes-where).
- **Documentation hot-links** (`https://wgse.bio/wgsextract-cli/...`) open in
  your web browser only when you click them. Your browser's own privacy settings
  apply.

## Local-only guarantees

- **Loopback-only app communication:** The desktop window and its local Node
  backend communicate strictly over authenticated `127.0.0.1` loopback sockets
  on your computer.
- **Self-contained reports and cards:** HTML result cards and personal reports
  do not load remote scripts, fonts, or images automatically.
- **No silent `allow_reference_downloads`:** On [Settings](settings.md)
  (Advanced mode → **Users Workspace Settings**), **Allow missing public-data
  downloads** is **off by default** and stays off unless you explicitly enable
  it.
- **No automatic remote size probes:** Opening [Reference Genomes](reference-genomes.md)
  uses local catalog metadata and cached sizes only; remote size lookup runs
  only if you explicitly invoke and confirm **Look up sizes**.
- **Local Genome Viewer & Gene Page:** Both run offline against files on your
  machine. The optional remote Genome Viewer never connects without your
  explicit connection and transfer confirmation, keeps your server token out of
  browser-delivered data, and enforces HTTPS certificate validation.

## How to run completely offline

If you work in an air-gapped environment or want **zero** outbound network
traffic — including no IP-visible update checks:

1. Choose **Not now** at first launch, or turn on **Privacy mode** in
   **Settings → Network & Privacy**. The red **PRIVACY** badge shows when
   Privacy mode is on. Avoid manual network actions, which Privacy mode
   does not block.
2. Keep **Allow missing public-data downloads** turned **off** in
   **Settings → Users Workspace Settings** (this is already the default).
3. Pre-install the tools, reference genomes, and reference data you need while
   online (using **Setup**, **Tools**, **Reference Genomes**, and **Reference
   Data**).
4. Optionally verify readiness before disconnecting by running
   `wgsextract deps ready -- <command ...>` in a terminal — it checks every
   tool, genome, index, and database completely offline. See
   [Checking a run before going offline](../../docs/privacy.md#checking-a-run-before-going-offline).
