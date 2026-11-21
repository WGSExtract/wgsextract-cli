## Choosing and editing a config

`--config FILE` works anywhere on the command line and selects the file for
both reading and saving, before validation. An invalid unused default cannot
block a valid alternate file; a bundling tool can choose a separate config.
The CLI and desktop app otherwise share the users-workspace config described
above. Former per-user locations, still recognized for existing installs, are:

- macOS: `~/.config/wgsextract/config.toml` if that file exists, otherwise
  `~/Library/Application Support/wgsextract/config.toml` if present;
- Linux: `~/.config/wgsextract/config.toml`;
- Windows: `%LocalAppData%\theontho\wgsextract\config.toml`.

Saves are atomic; `wgsextract config` prompts only in an interactive terminal.
Boolean values must be unquoted `true`/`false`, never `"false"`.
Threads accept a positive integer or `"auto"`; legacy numeric strings such
as `"4"` still work. Integer thresholds/TTLs require integers, not quoted
numbers or booleans. Memory (`"2G"`), paths and image names are strings;
`vcf_input_paths`/`server_allowed_paths` also accept string arrays.
Ports, job counts and thresholds must be in range; choice settings
(`server_mcp_transport`, `microarray_quirks`, `native_aligners`) must match
their listed choices. Aliases obey the same rules as canonical keys.

Invalid configured thread counts warn and use automatic threads, unless
explicit `--threads` overrides them; saves still require a valid count.
Other invalid types stop startup with one concise line naming key, file and
expected type. There is no `config set` or `config show` repair command:
edit the file, or choose a valid `--config PATH`, before rerunning even help
or `config`. Missing-setting defaults and valid values are unchanged.

## Example

```toml
# Defaults for --input and --outdir
input = "/data/genomes/joe/joe.cram"
outdir = "/data/wgse-out"

# FASTA (--ref); reference library; reference genomes only
ref = "/data/reference/genomes/hs38.fa.gz"
reflib = "/data/reference"
reference_genome_library = "/data/reference/genomes"

# One folder per person/sample, selected by --genome
dataset_library = "/data/datasets"

threads = "auto"  # Apple Silicon per-stage core plan; all cores elsewhere
memory = "auto"   # sort memory per thread; see hardware guide

# Optional external-tool overrides (otherwise use PATH)
haplogrep_path = "/path/to/haplogrep3"
haplogrep_tree = "phylotree-fu-rcrs"  # or phylotree-rcrs, mitotree-rcrs
y_tree = "yfull"                    # or yfull_v10, ftdna, isogg, [y_trees] id
vep_cache = "~/.vep"
```

Short keys are aliases: `wgsextract config` displays `input_path`,
`output_directory`, `reference_fasta`, `reference_library`, `cpu_threads`,
`memory_limit` and the other canonical names. Either spelling works;
explicit command-line options win for that run. Other settings include
annotation paths (`clinvar_vcf_path`, `gnomad_vcf_path`, `gnomad_sv_path`,
`gnomad_lr_sv_path`, `revel_tsv_path`, …), `mother_vcf`/`father_vcf`, and
the `[dev]` settings below.

By default the reference library is `reference/` in the app data folder;
reference genomes are its `genomes/`. A `--ref` FASTA chooses sequence.
When it is in the configured library's `reference_genome_library` (else
`genomes/`), that library supplies other assets. Only genomes and their derived
files go there; datasets belong in `dataset_library`. Otherwise the FASTA's
folder is searched first, then configured `reference_library`, for chains,
ploidy files, chip templates and annotations. Missing-file errors name the
searched folders. See [dataset discovery and example genomes](../sample-workspace.md#dataset-library).

For resource defaults, `memory = "auto"` starts from 25% of RAM split across
threads, subject to floors/caps; `"2G"` sets a per-thread budget. See
[threads and memory](../hardware.md#threads-and-memory) rather than treating it
as a whole-pipeline RAM cap.

## Temporary files and atomic outputs

Set `temp_directory = "/data/scratch"` or pass `--tmp-dir DIRECTORY` for
samtools/sambamba sort spills, GATK temp files and realign staging.
It must exist and be writable. Each run owns a workspace, normally removed
on success/failure; final files stay in `--outdir`. If realign fails after
extracting FASTQs, it retains the workspace and prints `--r1`/`--r2` reuse
paths; remove it after recovery. `--keep-fastq` instead keeps extracted reads
in the output directory. Unset defaults/tool arguments are unchanged.
Native pbmm2's sorter cannot honor this option: omit it or use minimap2.
This controls placement/capacity, not promised speed.

BWA/bwa-mem2 indexes require all nonempty index files, build under an exclusive
lock with a staged prefix, and rebuild after interruption rather than trusting
partial files. See [hardware-specific index requirements](../hardware.md#cpu-aligners)
for native versions that do not need `.0123`.

`align` always validates and publishes atomically, even with `--no-atomic-output`,
so validation failure preserves previous alignments. `vcf to-fasta` also stages
its FASTA, indexes, and requested chain beside their destinations; if generation
or indexing fails, the previous files are unchanged. A hidden lock file stays
in the output directory so concurrent retries cannot mix results.
`--atomic-output` (or
`atomic_outputs = true`) opts realign, SNP/InDel/GATK/FreeBayes callers and
main extract writers into rename publication, with indexes generated afterward.
Failed writes preserve previous files. Other commands keep the opt-in default:
staged paths alter tool provenance headers and can need extra disk.
`--no-atomic-output` disables their configured opt-in, not alignment or consensus
safety.
This adds no resume/skip behaviour; see [step journals](../step-resume.md).

## Keep-awake

`--keep-awake` or `keep_awake = true` requests idle/sleep inhibition until
exit. `--no-keep-awake` overrides config, including delegated analyze,
annotation-chain and benchmark commands; `keep_awake = false` is the default.
macOS uses `caffeinate`, Linux uses already-installed/permitted
`systemd-inhibit`, and native Windows uses `SetThreadExecutionState`.
Missing helpers, denied permissions or cleanup errors never fail the run.
This prevents automatic sleep, not shutdown or forced/manual suspension;
display sleep is allowed. Report-only help/config/deps never inhibit sleep.
