# Privacy and network access

> **Scope:** This document covers **WGS Extract CLI** (the command-line
> back-end, Python API and local server). For the desktop app's privacy model —
> including automatic update checks, interactive missing-data install prompts,
> and how the GUI sits atop the strictly offline CLI — see
> [Desktop App Privacy and network access](../gui/docs/privacy.md) and
> [GUI Architecture — Privacy and network boundary](../gui/devdocs/architecture.md#privacy-and-network-boundary).

Ordinary analysis, inspection and reporting stay on this computer. Defaults
must not attempt a connection, even when a reference, tree, gene list or
model is missing. There is no telemetry, usage reporting or background CLI
update check. The one exception is the desktop app's startup update check,
which asks GitHub whether a newer release exists and is off in **Privacy
mode** (below). Missing dependencies are reported, never installed by analysis.
Runtime Pixi invocations use both `--no-install` and `--frozen`: skipping
installation alone would still allow lockfile resolution and registry queries.
Incomplete installed runtimes fail with setup guidance, rather than falling back
to a potentially different CLI on the host's PATH.

## CLI network mode

Set `network_mode` in `config.toml`, or override it for one invocation with
`--network full|low|off`.

| Mode | Outbound behavior |
| --- | --- |
| `full` (default) | Existing operation-specific opt-ins apply. Selecting `full` alone does not grant consent. |
| `low` | Bulk transfers also require `--allow-large-download`. These include reference genomes and bootstrap archives, panels, models, annotation databases, package installs, and remote alignment or annotation streams. Small requests such as catalog size probes, release and checksum lookups, and pinned cytoband tables can still run with their usual opt-ins. |
| `off` | Refuses outbound operations even with `--download`, `--allow-large-download` or a disclosure opt-in. Installed data, cached metadata and checksum-verified staged downloads can still be reused locally. |

The CLI does not detect metered connections; the desktop app or another caller
can select the mode. The Python API and server accept `network` and
`allow_large_download` on operation parameters. A server job can be stricter,
but cannot relax the mode selected when `serve` starts. This setting governs
outbound operations, not the server's inbound listener or network access by
other programs.

## Download and disclosure opt-ins

Explicit download/install commands may use the network. Other commands need
`--download` before fetching missing public data. Without permission they
name the missing data and explain how to confirm the download. Installed,
verified data is reused offline. `--no-download` overrides a persistent opt-in:

```toml
# config.toml; default is false
allow_reference_downloads = false
```

`ref cytobands` validates installed tables offline and requires its own
explicit `--download` to fetch missing UCSC `cytoBand` data, even if the
persistent setting is enabled. The request contains only the hg19/hg38 public
assembly path on `hgdownload.soe.ucsc.edu`; no sample-derived data is sent.
The genome viewer uses installed tables only and never requests UCSC data.
`ref microarray-targets` also requires its own `--download` if a liftOver
chain needed to build the targets is missing.

`analyze comprehensive` runs these same guarded subcommands. Provision missing
data first with the download command named in the error, or explicitly opt in
through `config.toml` before running the comprehensive workflow.
The desktop exposes the same default-off setting under Settings → advanced
settings → **Allow missing public-data downloads**, with the request disclosure
in its tooltip.
The desktop app checks GitHub for a newer release when it starts. It is a plain
request for the public release feed (`latest.json`); it sends no sample
information, and the update is downloaded only after you choose to. Turn on
Settings → Standard UI Options → **Privacy mode** to stop it. The setting is
app-wide (the app's `settings.json`), so loading another bundle does not turn
it off. A red **PRIVACY** badge under the app's name shows while it is on, and
**File → Check for Updates…** still checks when asked. A development build
without an updater key has no feed and never checks.
Lineage and microarray pages also offer an unchecked per-run download option.
Fresh-install automation plans select it explicitly; ordinary runs do not.

The option is shared by the CLI and typed Python/server parameters. The
read-only MCP surface refuses download permissions. A server job needs both
job authorization and download permission; merely starting the server grants
neither to an analysis. Do not enable downloads globally on a machine that
must remain offline.

## Cohort disclosure defaults

[`cohort summarize`](cohort.md) reads and writes local files only. It does
not upload, query a remote service, or offer network sharing. `--export`
writes a separate local file, not a transfer. Keep all cohort files and
temporary work on an access-controlled disk outside synced folders and
network mounts; the CLI cannot tell whether a directory is synced.

The exact summary keeps all rows and flags those that fall below the
disclosure minimums. The optional export omits those rows entirely. These
top-level `config.toml` settings can be overridden for a single run:

| Setting | CLI option | Default and effect |
| --- | --- | --- |
| `cohort_min_samples` | `--min-samples` | **21** participants in the cohort and **21** callable participants at each exported row. |
| `cohort_min_allele_count` | `--min-allele-count` | **10** called copies of the target ALT at each exported row. |
| `cohort_min_carriers` | `--min-carriers` | **10** distinct carriers and **10** callable non-carriers at each exported row. A homozygote counts as one carrier, and a call carrying only another ALT counts as a non-carrier. |
| `cohort_min_relatedness` | `--min-relatedness` | **0.09** (twice kinship), the existing [`qc relate`](relate.md) third-degree cutoff. This warns; it does not filter rows or remove relatives. |

The **21** minimum follows the first positive count permitted by the
[NIH All of Us Data and Statistics Dissemination Policy](https://support.researchallofus.org/hc/en-us/articles/22346276580372-Data-and-Statistics-Dissemination-Policy).
The symmetric **10**-person cells use the
[ONS Secure Research Service output-checking guidance](https://www.ons.gov.uk/file?uri=/aboutus/whatwedo/statistics/requestingstatistics/secureresearchservice/gettingyourresearchoutputsapproved/srsoutputcheckingguidance.february2025.pdf)
as a starting point. The **10** ALT-copy minimum is a separate, configurable
check on allele copies, not an independent-person privacy rule. These are
local screening defaults, not compliance with either policy.

The result manifest records the active thresholds and number of flagged
rows. `cohort_small_cohort` warns about cohort size; `cohort_low_callable_samples`,
`cohort_low_allele_count`, `cohort_low_carriers`, and `cohort_low_non_carriers`
report counts of flagged rows by reason without listing variants. A row can
trigger more than one warning reason. `cohort_related_participants` reports
pairs meeting the cutoff without naming them. It uses only matched, assessed
pair estimates from an explicitly bound `qc relate` result
([Somalier](https://doi.org/10.1186/s13073-020-00761-2)). Missing, unmatched,
called-sites-only, or otherwise uncertain pairs are not evidence of
independence. If a QC result was supplied, `cohort_relatedness_unassessed`
reports incomplete pair coverage. An identified same-person pair still
fails the duplicate-input check; other QC findings do not automatically
exclude participants.

Keep the exact table, manifest and any private participant join private.
Even the reduced export is sensitive: allele presence, overlapping releases
and outside data can reveal membership or reconstruct suppressed counts.
The thresholds are not anonymization, approval to share, or a substitute for
checking consent, recipient permissions and disclosure risk.

## Checking a run before going offline

`wgsextract deps ready -- COMMAND ...` reports whether a command line can run
here without the network, before a long run or before disconnecting. It checks
each prerequisite with the check the command's own run makes: the platform,
the tools it runs (with their versions, Conda packages and the licences their
metadata record), Docker images in the local engine (identity, digests,
labels), NVIDIA GPUs and driver, Clair3 models and wgse-deepvariant weights
against their pinned SHA-256, and the input, reference genome, indexes and
annotation data. Each item is `runnable`, `installed`, `unverified`,
`unsupported`, `missing`, `stale` or `incompatible`; a missing one names the
explicit setup or download step that provides it (`deps install`,
`ref library --install`, `docker pull`, `ref verify`, `bam index`). The check
installs, pulls and downloads nothing and makes no network request; a command
that downloads, or a remote input, is reported as needing the network. It
exits 3 when the run isn't ready. Model and weight terms show as `unknown`
where nothing installed records them: installing, verifying or signing a
component doesn't grant a right to redistribute it. The server doesn't offer
the check, since its command line names paths on the host.

```sh
wgsextract deps ready -- vcf clair3 --input ont.bam --gpu
wgsextract --json deps ready -- vcf deepvariant --input sample.cram
```

## What goes where

Public-data requests reveal your IP address, requested asset/model identifiers,
HTTP headers and timing. GitHub downloads can redirect to GitHub's release
asset CDN; checksum discovery may request `api.github.com`. Audited
[resource mirrors](../src/wgsextract_cli/assets/resource_mirrors.json), when
available, use byte-identical public assets and the same consent. A mirror is
not permission to download. No reference download sends sample files,
genotypes or variant coordinates.

`vep run` uses a local cache and fails if the required Ensembl cache is
missing. Choosing `--engine ensembl` does not authorize sending variant
queries. The separate `vep download` command fetches public data only.

| Opt-in | Destination | Request data |
| --- | --- | --- |
| `qc ancestry --download` | GitHub WGSExtract releases/CDN | Pinned HGDP+1KG reference filename, not sample ancestry or positions |
| `microarray --download` | `hgdownload.soe.ucsc.edu`; GitHub WGSExtract releases/CDN as mirror | Whole hg18→hg19 / hg19→hg38 chain filenames; sample coordinates are lifted locally and never sent |
| `ref microarray-targets --download` | Same UCSC hosts and mirrors as `microarray` | Whole liftOver chains for building public chip targets; the command never reads sample data |
| `vcf pharmcat --download` | `zenodo.org`, via PharmCAT's reference downloader (record 7288118) | Public GRCh38 reference archive/index filenames, not pharmacogenetic variants or sample files |
| `ref index --aligner bwa-mem2 --download` | Published hs38DH index on Amazon S3 | Reference index filenames, not read/sample information; without permission the index is built locally if RAM permits |
| `typing hla --download` | IPD-IMGT/HLA's public distribution, via T1K (GitHub ANHIG/IMGTHLA) | Allele-database identifiers, not reads or HLA calls |
| `typing target-cnv --download` | NCBI (`ftp-trace.ncbi.nlm.nih.gov`), GIAB v3.5 stratifications | Assembly name in two fixed BED URLs; no target intervals, reads, sample identifiers or calls |
| `lineage mt-haplogroup --download` | GitHub `genepi/phylotree-fu-rcrs`, `genepi/phylotree-rcrs-17`, `genebygene/mitotree` releases/CDN | Selected tree ID/version, not haplogroup or variants |
| `lineage y-haplogroup --download` | GitHub WGSExtract releases/CDN; configured custom-tree host | Tree ID/version and selected reference build/table; custom-tree URL, not sample markers/calls |
| `report --download` | GitHub WGSExtract releases/CDN | Selected public gene-list filename/version, not findings |
| `report --pgx-context FILE --download` | `files.cpicpgx.org` | The pinned CPIC database release filename (`cpic_db_dump-v1.61.0.sql.gz`), not the medication list, findings or genotypes; the medication file is read locally only |
| `ref recheck --online` | `ftp.ncbi.nlm.nih.gov` (ClinVar's weekly release listing for the recorded build) | A plain GET of the public listing URL; no sample name, paths, positions, genotypes or findings. Without `--online` (or with `--answers FILE`) nothing is contacted; see [recheck-and-delta.md](recheck-and-delta.md) |
| `score pgs --download` | `www.pgscatalog.org`, `ftp.ebi.ac.uk`, GitHub WGSExtract releases/CDN | PGS ID, coordinate build, public scoring weights and calibration/ancestry asset names, not sample scores or genotypes |
| `basecall --download` | Oxford Nanopore's Dorado model distribution | Model/modification names and POD5 chemistry metadata used to select a model. No signal/read files are uploaded. For offline use pass a local `--model` directory without `--modified-bases`. |
| `methylation regions --download` | `hgdownload.soe.ucsc.edu` | Pinned public hg38 `cpgIslandExt` filename; no sample positions or calls. The flag is local-only, and the server can read an allowed, preinstalled table. |
| `ref library --remote-sizes` / desktop **Look up sizes** | Each catalog entry's host, including UCSC, NCBI and EBI | HTTP(S) HEAD or FTP SIZE requests for public reference filenames with missing or at least 30-day-old cached sizes; no sample data |
| `realign --input https://…` | The URL's host and redirects | Explicitly selected remote alignment/index; HEAD, index download and sample-derived genomic byte ranges. Use a local file to avoid disclosing access patterns. Server clients cannot supply URLs. |
| `benchmark --dataset real` / `--dataset-url` | Selected public benchmark host | Dataset/index URLs; this explicitly selected dataset mode downloads public test data |
| Desktop startup update check (off in **Privacy mode**), **Check for Updates…**, then **Download** | Configured GitHub release/update endpoints/CDN | Application version, platform/architecture and asset requests, not sample information |
| Desktop **Setup**, reference/dataset download actions | GitHub, Pixi/Conda/PyPI registries and each selected resource publisher | Selected package versions, platform and public dataset identifiers, not sample information |
| Desktop genome viewer **Connect and view remote file**, after transfer consent | The operator-selected server URL displayed in the viewer | Selected server-side sample/index/reference paths, adjacent FASTA index paths and genomic byte offsets; the backend authenticates with the server token and receives selected genomic/reference bytes |

Opening an external report/documentation link is an explicit browser action;
the browser's own privacy settings apply. Generated reports do not load remote
scripts, fonts or images automatically. The desktop's UI and job server talk
over authenticated loopback connections on this computer. The optional
[remote genome viewer](../gui/docs/genome-viewer.md#view-files-on-a-remote-server)
uses that local backend to proxy explicitly selected server file ranges.
Importing or restoring its private TOML configuration makes no remote request.
The backend keeps the server token outside browser-delivered data; the viewer
receives revocable per-file grants. HTTPS retains certificate validation;
redirects and cookie forwarding are disabled. Plain HTTP exposes credentials
and genomic traffic, so use an operator-managed TLS proxy or encrypted tunnel.
Opening the desktop's Reference Genomes page reads only local/catalog metadata
and cached sizes, including old sizes. **Look up sizes** requires confirmation
of the destinations and requests before any remote size lookup.

## Audit for issue #1052 (offline defaults)

Baseline: main `28f56cb9`, 2026-11-17. This table groups commands that share
the same execution path; options not listed inherit their command's local
behavior. Classification describes the baseline, before this change.

| Command/option | Network on baseline? | Classification / disposition |
| --- | --- | --- |
| `help`, version, `config`, `sample`, `report-issue`, `info`, coverage, chip inspection | No | Local; no install/update/size probe; support builds a redacted report but does not upload it |
| `extract` and `bam` families, `repair`, `align`, local-input `realign`, `panel`, `sign`, `analyze batch-gen`, VCF conversion/calling/filtering/comparison/phasing/imputation, QC fake-data/fastp/FastQC/MultiQC/VCF/somalier/contamination, non-HLA typing, Y-STR, PET, methylation | No | Local; absent references, annotation data, panels and tools give guidance |
| CRAM decoding by htslib/Java tools | No remote fallback intended | Local-only `REF_PATH` retained; no Java reference-download property; missing references fail |
| Liftover/chip coordinates | Coordinate conversion is local; missing microarray chains were fetched | **Default-violation** found in review: missing whole-chain downloads now require `microarray --download`; pyliftover receives only local chain files |
| VEP run/verify, chain-annotate, ClinVar/REVEL/gnomAD/SpliceAI/etc. annotation | Ensembl VEP could fall back to online annotation if its cache directory was missing | Local caches/indexes only; a missing cache now stops `vep run` and names `vep download` |
| DeepVariant/Clair3/Parabricks containers and native callers | No registry pull | Installed images/models required. Local compute containers use `--network=none` and `--pull=never` in every mode; no runtime dependency installation. |
| `ref download`, genome-library install, bootstrap, annotation resources, gene maps, microarray targets, ancestry/AADR/somalier/imputation/yBrowse/gnomAD-SV install, ACMG data preparation | Yes | Explicit network operations; retain downloads and mirrors |
| Reference/dataset listing/status/verify/count/delete | No | Local metadata; remote sizes only with the opt-in above |
| `ref index --aligner bwa-mem2` on hs38DH | Yes if published index absent | **Default-violation** found in independent review: fetching prebuilt indexes now requires `--download`; local index building stays available |
| `vep download`, `example-genome download` | Yes | Explicit network operations; retain downloads |
| `qc ancestry`, calibrated `score pgs` | Yes when reference absent | **Default-violation**: require `--download` |
| `score pgs --trait/--pgs-id` | Yes, including repeated runs | **Default-violation**: require `--download` for absent Catalog files; reuse local copies |
| `typing hla` without local index | Yes | **Default-violation**: require `--download` for IPD-IMGT/HLA |
| `vcf pharmcat` without its reference FASTA | Yes, in PharmCAT's downloader subprocess | **Default-violation** found in review: require `--download` before starting the subprocess |
| `lineage mt-haplogroup`, `lineage y-haplogroup`, custom remote Y trees | Yes when data absent | **Default-violation**: require `--download`; cache reuse remains local |
| `analyze comprehensive` | Inherits first-use tree downloads from lineage steps | **Default-violation**: child commands now refuse missing data without consent; pre-provision it or explicitly opt in through config |
| `report` with clinical findings and absent selected gene list | Yes | **Default-violation**: require `--download`, not silent partial placement |
| `report --pgx-context` with the pinned CPIC release absent | Yes | Requires `--download`; the medication list never leaves the computer |
| Dorado `basecall` model names/modifications | Yes | **Default-violation**: require `--download` or a local model directory |
| Remote realignment input, remote sizes, benchmark real-data selection | Yes | Explicit **opt-in**; request data disclosed above |
| `serve` HTTP/MCP, desktop local backend/path picker | Loopback only | Explicit local transport, no automatic remote call; stdio MCP opens no listener |
| Desktop launch | GitHub update check | **Default on, by owner decision**: one request for the public release feed, no sample data; **Privacy mode** turns it off, and tests set `GFC_TAURI_AUTO_UPDATE=0` |
| Desktop Reference Genomes page rendering | Automatically ran `ref library --list --remote-sizes`, querying catalog hosts when sizes were missing/stale | **Default-violation** found in independent review: the automatic data source now uses cached sizes only; a separate, confirmed **Look up sizes** action opts in |
| `deps check` / shared optional-tool discovery and version probes | Pixi could resolve a changed manifest | **Default-violation**: frozen lockfile queries/probes, without environment installation |
| Installed CLI/desktop analysis launchers | Pixi could complete/update environments or resolve a changed manifest | **Default-violation**: always `--no-install --frozen`; Setup remains explicit |
| CLI/app telemetry | None found | No default telemetry endpoint |

PR #1019 (ACMG site-restricted annotation data) is coordinated separately:
its proposed remote tabix reads reveal sample-derived variant byte ranges to
Google Cloud Storage and Ensembl. That must never be an automatic fallback,
including on a missing local database. Its explicit sample-position permission
is distinct from permission to download a complete public reference. On the
audited main revision, `vcf acmg` requires local prepared databases; the
SpliceAI auto-fetch belongs to the explicit `ref acmg-data` preparation command.

Regression coverage blocks Python DNS/socket connections while representative
local commands process tiny synthetic data, checks first-use refusal and
explicit consent, and retains real samtools/bcftools. Subprocess network
isolation is used where the host permits an unprivileged network namespace;
socket monkeypatches alone do not sandbox C extensions or child processes.
