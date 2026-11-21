# Running commands

Start with the [README quick start](../README.md#quick-start).
For every command and its outputs, use the generated [command reference](commands.md).

## Finding a command

`wgsextract --help` orders core commands by workflow:
`info` → `microarray` / `lineage` → `extract` → `realign` / `align` →
`vcf` → `vep` → `report`. Setup (`ref`, `deps`, `example-genome`, `config`)
follows; advanced commands (optional or hard-to-install tools, format repair,
benchmarks) are listed by name. The command reference starts from what you
have: BAM/CRAM, FASTQ, VCF or a raw-data file.

- `wgsextract help --all`: one line per command, grouped by tier;
  `help --all vcf` limits it to a group.
- `wgsextract help vcf snp` or `wgsextract vcf snp --help`: options, examples
  and outputs. `--help-all` adds tuning and unusual-data options.
- `wgsextract help topics`: concepts, including `help references`,
  `help dataset-library`, `help exit-codes` and `help manifests`.
- `wgsextract help`: the complete command tree.

For agents, navigate **index → command**. `help --all --json` is the compact
`wgsextract.help-index/1` index: paths, summaries, tiers and key flags.
`help <command> --json` is `wgsextract.help/1`: typed options, choices,
defaults, examples and outputs, plus an `input_schema` for commands on the
[typed API](api.md). Index flags are discovery hints, not complete invocations.
`help --all --json --full` opts into the complete schema dump.
Fields may be added within a schema version; renaming or removing one requires
a new version.

Mistyped commands, options and choices exit 2 with a “Did you mean” hint.
On a terminal, some commands print “Next:” suggestions on stderr, never with
`--json`, `--quiet` or piped output. Piped help is plain text; `NO_COLOR`
disables colour.

## Options and results

Global options (`--input`, `--outdir`, `--genome`, `--ref`, `--threads`,
`--memory`, `--json`, `--quiet`, `--debug`, `--log-file`, and those in
`wgsextract --help`) work before or after a command, at any depth.
`wgsextract vcf snp --input x.bam` equals `wgsextract --input x.bam vcf snp`.
Different values before and after the command are a usage error; repeated
in the same position, the last wins. A command's help lists globals in one line.

Try a small region (`--region chrM`, where supported) before a whole genome.
Large jobs can take hours and hundreds of gigabytes; see
[resource estimates](resource_estimates.md). Exit codes are 1 for failure,
2 for usage errors (including missing input), and 3 for missing, unusable or
too-old required tools. They make commands safe to chain in scripts.

File-writing commands record `<output>.manifest.json`: inputs and outputs
with cheap fingerprints, metrics, structured warnings and provenance
(versions, parameters, reference and timing). `--json` prints the result
document instead of human output; logs stay on stderr.
See the [`wgsextract.result/1` schema and codes](result-manifest.md).
The typed `wgsextract_cli.api` returns a `Result`; migration includes `info`,
`vcf snp`/`indel`, `lineage` and `microarray`.

Outputs use the full input stem: `HG002.chr20.bam` produces
`HG002.chr20_sorted.bam` and `HG002.chr20_coverage.tsv`.
Use `--outdir`, configured `outdir`, or the `--genome` folder; see
[configuration](configuration.md) for the default `wgse/` output folder.
Detected sample sex, platform, build and depth are fingerprint-bound facts in
[`wgsextract.sample.toml`](sample-workspace.md), reused rather than redetected.
Correct them with, for example, `wgsextract sample set --genome ID --sex Female`.

Long workflows (`realign`, `vcf deepvariant`, `analyze comprehensive`) keep
verified steps in `.steps.json` journals. Rerunning after a crash, Ctrl-C or
OOM skips completed steps. `--dry-run` explains the plan, `--force-step STEP`
reruns that step and later ones, and `--force` reruns all. See [resuming](step-resume.md).

`analyze comprehensive` uses `vcf_input_paths` from config only when you
don't select inputs. A configured `input_path` can be used alongside these
VCFs. With a BAM/CRAM selected via `--input` or `--genome`, it calls variants
from that alignment unless you supply matching `--vcf-inputs` or use
`--skip-calling`.

When VCFs in one comprehensive run would have the same output name, each
gets a numbered suffix. VCFs whose names don't collide keep their usual
output names. The summary shows the source path for each result, and the
manifest records it as `source_vcf` on each significant-variants output.

`wgsextract report --genome ID` reads earlier manifests without running tools.
Its local HTML, Markdown, JSON and findings table cover overview/QC, Y/mtDNA,
pharmacogenomics (including HLA-A/B), ClinVar pathogenic calls in ACMG SF v3.3
or chosen genes (including recessive carrier/biallelic status), and microarrays.
Findings link confidence and evidence; unrun sections show the next command.
These are technical results, not diagnoses. See [personal reports](personal-report.md).

For other programs, [`wgsextract serve`](server.md) exposes local HTTP/JSON
on 127.0.0.1 with a per-launch token, background jobs, progress, cancellation
and OpenAPI, or MCP (`--mcp`, stdio or streamable HTTP). Requests can access
only allowed folders.

`sign create` writes `SHA256SUMS`, also readable by `sha256sum -c`.
`--key` signs it with `ssh-keygen -Y sign`; `sign verify` checks the signature
against a trusted key, then re-hashes every file.

## Progress and logging

Alignment/realignment and GATK scatter print a stderr heartbeat every 60 s:
processed versus expected reads when known, or completed shards, elapsed time
and rate-based ETA. Sampled FASTQ counts are estimates; reaching the count
does not mean sorting/indexing finished. This changes neither stdout data,
tool arguments nor genomic files. Contig-parallel calling (`vcf snp`/`indel`
and multithreaded `microarray`) reports each completed contig on the terminal
progress line and as a `contig` event.

Persistent logging is off by default. Choose a **new, dedicated** path:

```sh
pixi run wgsextract info --input sample.bam --log-file out/run.log
```

Or set `log_dir = "~/wgse_logs"` in `config.toml` for unique per-run logs.
The directory keeps its newest 100 generated logs and removes older ones.
The UTF-8 DEBUG log contains Python diagnostics and pipeline/scatter messages.
`run.log.provenance.json` records argv, CLI/platform versions, tool commands
and versions, and small reference-index signatures. It is available during
the run and finalized on normal exit or handled failure; a hard kill may leave
`running`. Existing logs are never truncated. `--quiet` suppresses console
information, not file logging.

Review logs before sharing: diagnostics may contain private paths or URLs.
Logging captures no environment, installs no tool and never hashes full genomes.

## Inspecting an alignment

`info` recognizes native Element AVITI
[Bases2Fastq read names](https://docs.elembio.io/docs/bases2fastq/outputs/#seq-identifiers-sequencing),
including legacy AV serials and `_AVITI` model suffixes. Accession-renamed
reads may remain `Unknown`.

`info` samples reads near chr1:20 Mb to report duplicate marking and fraction,
binned versus full-resolution base qualities, and ALT-aware mapping.
Callers, `microarray` and `vcf trio` warn about ALT-unaware inputs or absent
duplicate marking, as found in WGS Extract v4 alignments; `realign` fixes both.
Detailed info omits the uncomputed WES-depth row. Missing-reference errors
name the library install command when the alignment matches a catalog model;
no reference is downloaded automatically.

`info calculate-coverage --bins` replaces the normal summary with per-contig
counts/fractions at depth **0 / 1–3 / 4–7 / 8+**, also written to
`<input>_coverage_bins.tsv`. It includes unread contigs; `--region` makes
fractions describe that interval. One streaming `samtools depth` pass writes
no per-base depths. As in WGS Extract v4, base/mapping thresholds are zero;
unmapped, secondary, QC-failed and duplicate reads and deletion bases are
excluded, while overlapping mates count separately. A whole contig's
reference N bases (from the reference's `_ncnt.csv`, else the standard
assembly's counts) are left out of depth 0 and the denominators, once (v4's
double N subtraction is not reproduced), and shown as `Model N Len`; a partial
`--region` keeps every coordinate. `--with-bins` also runs the normal coverage
summary but shows the bin table (v4's "Bin Coverage by Sequence Name") in its
place; `info --detailed` then shows that run's breadth. Breadth everywhere is
likewise a share of the non-N bases. Without `--bins`, summary and cache
behaviour are unchanged.

Depth parsing uses bounded 8 MiB binary chunks and byte operations, not
per-base Python parsing or NumPy; TSVs and filters are unchanged.
CRAM bins require an existing FASTA from `--ref`, config or the library:
missing references fail before decoding, never triggering htslib downloads.
`--by panel.bed` instead writes per-target mean/median depth, fractions at
10x/20x/30x and `--min-depth`, plus on-/off-target enrichment, as
`<input>_<bed>_target_coverage.tsv` and `_target_summary.txt`;
see [target coverage](ont.md#adaptive-sampling-targets).

Disk preflight counts records from single-reference CRAM 3.0/3.1 container
headers, falling back to `samtools idxstats` for other versions, multi-reference
containers or invalid headers. Totals include placed and unplaced unmapped
reads. Detailed info and sex inference still use idxstats: container totals
cannot distinguish mapped from placed-unmapped reads.

## Reporting a problem

`report-issue` prepares a pre-filled [GitHub issue](https://github.com/WGSExtract/wgsextract-cli/issues/new/choose):

```bash
wgsextract report-issue
wgsextract report-issue --last-failed
wgsextract report-issue --run out/sample.vcf.gz.manifest.json --print
```

It includes command, exit code, error, warnings, last 40 log lines, CLI
version/install type, OS, CPU, RAM, free disk, tool versions and changed settings.
Local redaction removes home/user/computer names, sample/file/read names,
genotypes, VCF records, sequences, emails, tokens and secrets; paths become
placeholders such as `<path>/<file>.cram`. Sex and haplogroups are never included.

Nothing is uploaded. The report and link are printed, and the complete report
is saved as `issue-report-<time>.md` in the data folder's `state/reports`,
beside `reference` and `genomes` (or `state_directory`, or `--outdir`).
The browser opens only after yes or `--open`; `--print` never opens it;
`--json` exposes the result to scripts. Links stay below 8,000 characters
(`--max-url-length`); log tail, then warnings, settings and tool versions are
shortened with an omission notice so you can paste from the saved file.
Review before submitting. Python callers use
`api.support.build_issue_report(api.ReportIssueParams(...))`; see [API](api.md).
