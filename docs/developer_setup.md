# Developer setup

## Set up a machine

macOS or Linux:

```bash
git clone https://github.com/WGSExtract/wgsextract-cli.git
cd wgsextract-cli
./scripts/dev_setup.sh
```

Windows (PowerShell; no WSL or MSYS2 needed):

```powershell
git clone https://github.com/WGSExtract/wgsextract-cli.git
cd wgsextract-cli
powershell -ExecutionPolicy Bypass -File scripts\dev_setup.ps1
```

The script needs only git (and the Xcode Command Line Tools on macOS). It:

1. Installs [Pixi](https://pixi.sh) for your user if it's missing. Pixi provides Python and every bioinformatics tool.
2. Installs the `default` and `dev` Pixi environments exactly as locked in `pixi.lock`. The first run downloads a few GB.
3. Installs [just](https://just.systems) with `pixi global install just` if it's missing.
4. Installs the pre-commit hooks (ruff, shellcheck, gitleaks, whitespace).
5. Runs `wgsextract --version`, `wgsextract deps check` and the quick smoke tests.
6. Lists anything else worth having: a git identity, the GitHub CLI (`gh`), and an AI coding agent.

Pixi **0.82.0 or newer** is required (declared in `pixi.toml`, kept in
agreement with CI). Setup checks existing installations before loading the
manifest; it never updates them automatically. If yours is older, run
`pixi self-update` and rerun setup.

`deps check` marks tools in optional Pixi environments (GATK, VEP, DeepVariant, ...) as missing. That's expected: install one when you need it with the `pixi install` command `deps check` prints. When the script installs Pixi or `just`, open a new terminal before using them (or add `~/.pixi/bin`, `%USERPROFILE%\.pixi\bin` on Windows, to `PATH`). Rerun the script at any time; `just setup` does the same once `just` is installed. `--skip-tests` (`-SkipTests` on Windows) skips the smoke tests.

### Pixi environments

Optional tools live in separate environments. From a source checkout or the
installed `wgsextract-cli/app/`, run
`pixi install --locked -e <environment>` explicitly before use.
Feature commands never install dependencies: missing tools name the exact
install command, including the app manifest path so it works from any directory.
Installed tools outside PATH launch with `pixi run --no-install -e <environment>`.
You may also run a whole command inside its installed environment.

| Environment | Used for |
| :--- | :--- |
| `default` | Standard commands |
| `pacbio` | pbmm2, pbsv, Sniffles; `pixi run -e pacbio wgsextract vcf sv --pacbio` |
| `vep-rs` | Default `vep run` / `vep download`, all platforms; [engines](vep-engines.md) |
| `vep` | `vep run --engine ensembl`, not native Windows |
| `cyrius`, `smn`, `t1k`, `expansionhunter`, `longtr`, `strkit` | CYP2D6, SMN, HLA and repeat typing, Linux/macOS; [requirements/licences](variants.md#pharmacogenomics-and-repeat-typing) |
| `pharmcat` | PharmCAT Python/Java/bcftools runtime |
| `modkit` | Methylation pileup, Linux/macOS |
| `verifybamid2` | Contamination QC, Linux/macOS |
| `parquet` | pyarrow for `--format parquet`, e.g. `pixi run -e parquet wgsextract vcf table ... --format parquet`; no Windows ARM64; [details](parquet.md) |
| `dev` | pytest, Ruff, mypy; automatically selected by development tasks |

Other optional environments are documented with their features:
[hardware](hardware.md), [ONT](ont.md), [MultiQC](interop.md#multiqc),
[relatedness](relate.md), [imputation](imputation.md) and [ACMG](acmg.md).
Cyrius permits noncommercial use only, no distribution/modification, and
is not bundled.

On macOS, `bio-tools` environments and the separate `pharmcat` environment
use Apple's Accelerate BLAS so `bcftools` does not load LLVM OpenMP through
GSL. Before starting workers, pytest removes only current-user, unwritten
OpenMP registration files whose PID is absent. For other tools and manual
cleanup, see [OpenMP troubleshooting](installation.md#macos-openmp-error-15-with-unknown-library).

### Benchmarking on macOS

Setup creates `.metadata_never_index` in `out/`, `scratch/` and `tmp/`
(safe to rerun). For existing indexed trees, also add those folders or the
repository to **System Settings → Spotlight → Search Privacy**. Keep heavy
benchmark inputs and outputs out of Photos libraries and Spotlight-indexed
folders. Check volume indexing with `mdutil -s /` and thermal pressure with
`pmset -g therm` before timing; wait for indexing and throttling to settle.

## Make a change

```bash
git switch -c my-change
# edit code
just run info --input sample.bam   # try the CLI from this checkout
just check                         # format, lint, typecheck, test
just ci                            # what CI runs; do this before opening a PR
git commit -am "Describe the change"
gh pr create --fill
```

`just` lists every recipe. Each wraps a `pixi run` task, so `pixi run test` and friends work without `just`.

### Checks and test data

Developer tasks select `dev` automatically:

| Task | What it runs |
| :--- | :--- |
| `pixi run test` | Unit/integration tests, excluding end-to-end suites |
| `pixi run test-ci` | Parallel tests, timings/JUnit and non-gating coverage under `out/` |
| `pixi run smoke-test` | CLI smoke; `smoke-test-full` adds full-genome/pet tests |
| `pixi run test-e2e` | Real-data end-to-end suites |
| `pixi run test-e2e-fake` | Generated-data end-to-end (`pytest --fake-data`), also CI on Linux/macOS/Windows |
| `pixi run test-remote` | Weekly live download availability, SpliceAI format and HG00096 kit/Omni concordance |
| `pixi run lint` | Ruff, site-source and GitHub Actions validation |
| `pixi run format` | `ruff format .` |
| `pixi run typecheck` | Strict mypy on `src/wgsextract_cli` |
| `pixi run site-build` | Rebuild [site](site_generation.md) |
| `pixi run benchmark-dataset` | Rebuild release-backed mini-genome |
| `pixi run stats` | Statistics; install [cloc](https://github.com/AlDanial/cloc) separately |

With [just](https://github.com/casey/just), `just run <args>` runs the CLI.
`just test`, `lint`, `fmt`, `typecheck`
and `check` (all four) are aliases; `just ci` runs lint/type checks,
regular tests and generated small/full-genome integration without formatting,
keeping pytest scratch under `out/`.

PR/main CI is bounded and cancels superseded runs. A separate lint-only
environment runs Ruff, workflows and strict mypy without reference downloads;
genomic jobs cache public hs38. A CI policy test keeps the mypy exemption
baseline for `wgsextract_cli.core` empty: don't add overrides that match it
or relax the global strict configuration in `pyproject.toml`.
Action updates retain full commit SHAs and
version comments from allow-listed sources, not a duplicate validator SHA
table. See [CI routing](ci_routing.md).

The weekly **Pixi refresh** resolves within manifest pins, restores declared
archive digests and validates the lock candidate with a read-only token.
It force-pushes validated changes to fixed `automation/pixi-refresh` and
opens/updates one PR; no change opens none and closes a superseded PR.
CI waits for maintainer approval (“Approve workflows to run” or the PR-body
command), then runs normal checks. It never auto-merges dependencies.
Numerical-tool updates still need public small/full-genome byte-parity checks.

Real-data tests use pytest options or config:

```bash
pixi run pytest tests/test_smoke_real_basics.py \
  --real-input /data/HG002.cram --real-ref /data/reference/hs38.fa --real-region chr20
```

Options include `--real-input`, `--real-ref`, `--real-vcf`, `--real-region`,
`--real-targets`, `--real-fastq-r1`/`--real-fastq-r2`, `--fake-data` and
`--full-data` (see `tests/conftest.py`). Or set `real_genome_test_path` and
`real_genome_tests_enabled = true` in `[dev]` at the end of config.toml.
`download_cache = true` caches downloads under
`$XDG_CACHE_HOME/wgsextract/downloads` (default `~/.cache/wgsextract/downloads`),
pruned after `download_cache_ttl_seconds` (default one week).
Former top-level settings, including `dev_download_cache` and
`dev_download_cache_ttl_seconds`, now fail with their `[dev]` replacement.

`wgsextract qc fake-data` generates BAM/CRAM/VCF/FASTQ for tests and reports;
see [testing workflows](https://wgsextract.github.io/wgsextract-cli/workflows.html#testing).

### Filesystem paths

Use `pathlib.Path`. Shared helpers accept strings/`os.PathLike[str]`
(`core.paths.PathInput`); keep Paths during manipulation, converting at
string-based CLI/config/tool-wrapper/public-return boundaries.
Use `input_stem`/`fasta_prefix` for compound genomic extensions and
`append_suffix` for `.vcf.gz.tbi`/`.cram.crai` sidecars.
`absolute_path` normalizes `..` without following symlinks; `Path.resolve()`
compares real targets. Wrapper commands and samtools `out##idx##index`
arguments stay strings; do not convert native Windows paths to POSIX spelling.

### The desktop app

The WGS Extract desktop app is in `gui/`. It has its own toolchain (uv, Node and Rust) and setup; see [gui/docs/developer_setup.md](../gui/docs/developer_setup.md). From the repository root:

```bash
just gui-setup        # install uv, Node and Rust if missing, and the app's locked dependencies
just gui-run          # build and serve the Web UI in a browser
just gui-tauri        # run the desktop app in dev mode
just gui-ci           # the app's lint and tests, as GUI CI runs them
just gui-package      # build this platform's installers into gui/out/release/tauri
```

Root ruff, mypy and pytest skip `gui/`; changes there run GUI CI instead of the CLI's CI, and changes elsewhere don't run GUI CI.

When you add or rename a command:

- Give a new top-level command a tier (core, setup or advanced) and a short
  summary in `COMMAND_TIERS` in `src/wgsextract_cli/commands/registry.py`.
- Give every runnable command an example and an outputs line in its command
  group's module under `src/wgsextract_cli/commands/_help_catalog/`.
- Register its parser in `src/wgsextract_cli/cli/registration/`, keeping
  execution imports inside handlers. `cli/_lazy.py` defers loading command
  implementations until execution; help must not import scientific libraries.
- Run `pixi run gen-command-docs` to regenerate the bundled
  `assets/cli/help-index.json` used for fast, configuration-independent discovery.
  Run `pixi run site-build` to preview the automatically generated command
  reference and the cards on the site's CLI page.

`tests/test_cli_help.py` checks the bundled index; `tests/test_site_references.py`
builds the site and checks the published references.

### Benchmark fixture ploidy

The `benchmark` timing workload explicitly calls SNPs, indels and microarray
sites with uniform diploid ploidy for generated data and the `real` mini-genome
archive. Scaled, regional and low-depth fixtures cannot reliably infer sex;
the benchmark is not a karyotype or genotype-accuracy validation. This override
is passed only to those child commands and does not change the sample's
declared/observed sex or weaken normal Unknown-sex refusal. Full-reference
`real-1x`/`real-30x` runs retain build-specific ploidy.

### VCF API specifications

`api/vcf.py` remains the public import and dispatch facade. Its parameter
declarations live in `api/vcf_specs/`: shared field helpers, calling, file
operations and annotations. Update the responsible specification module when
adding fields, and retain its explicit facade export. Parameter types keep
their physical source modules for introspection; facade aliases keep pre-split
pickle lookups working. Facade declaration order keeps the generated API
reference stable without duplicating parameter descriptions beside their handlers.

### Generated references and parallel PRs

Run `just regen` (or `pixi run python scripts/regen.py`) after changing CLI/API
help. It updates the CLI snapshot under `tests/fixtures/cli_snapshot/` and the
bundled compact discovery index. The snapshot stores one JSON file per covered
command (for example `vcf/snp.json`), including its parser, help and argv cases.
Different command files can merge independently; adding children to the same
group can still change that group's help. Regenerate rather than editing
snapshots by hand.
The [site build](site_generation.md) generates the public references and CLI
cards separately; they are not committed.
If a branch still tracks a retired generated reference, `just regen-resolve`
stages its deletion after you resolve source conflicts. In `site_src/cli.md`,
keep the `<!-- command cards -->` placeholder, not the old generated cards.
It also generates the public sample-workspace JSON Schema from the output
types in `core/sample_document.py`. Output types use `TypedDict`, dataclasses and
`Annotated` constraints; `core/output_schema.py` rejects unsupported annotations.
Its supported subset is TypedDict, dataclasses, Literal, Annotated, primitives,
unions, lists and string-keyed dicts; Enum, tuple and Mapping fields are
not yet supported. Dataclasses describe complete `dataclasses.asdict`-style
projections: every field, including nullable/defaulted and `init=False` fields,
is required in the output. `ClassVar`, `InitVar` and `KW_ONLY` pseudo-fields are
excluded before resolving annotations, including postponed Python 3.10 hints.
Real fields resolve in their declaring module and class/MRO namespaces.
Generation never constructs instances or calls default factories, and does not
infer field omission or custom `to_dict` behavior. Use an explicit serialized
model when a custom writer changes the field projection.
For recursive fields on Python 3.10, postpone the whole annotation
(`children: "list[Tree]"`), not only the element (`list["Tree"]`): that version's
`get_type_hints` leaves partial PEP 585 forward references unresolved.
Definition names and extension-key policies are supplied
explicitly to each generation call, never registered globally. Workspace
reads retain unrecognised TOML data in `WorkspaceState`; new fact/override/output
records are written through the typed document models rather than casting
unvalidated loaded data to those types.
Keep these committed: readers,
release archives and freshness tests need them without running Python.

The generators run by `scripts/regen.py` write LF on every platform, and
`.gitattributes` keeps their fully generated files at LF on checkout. The setup
scripts configure the `merge=generated` driver assigned to those files.
For an existing checkout, configure it once:

```bash
git config merge.generated.name "Keep generated references until regeneration"
git config merge.generated.driver true
```

This is an **ours-style placeholder**, not an automatic generator: a merge
driver runs before all source files are merged, so regeneration there could
silently use inconsistent sources. Merge locally with `git merge --no-commit
origin/main`, resolve and stage source conflicts, then run **`just regen-resolve`**
(or `pixi run python scripts/regen.py --resolve`). It regenerates all references
from the combined sources and stages them; it refuses unresolved source
conflicts. Review the diff, run the freshness tests and commit the merge.
The same command resolves generated-file conflicts without the driver installed.
Site command cards share a file with hand-written content, so resolve any
`site_src/cli.md` conflict as a source conflict before regenerating.

GitHub **does not execute custom merge drivers**. If it reports generated-file
conflicts, use the local workflow above and push the resolution; don't choose
one side in GitHub's conflict editor or expect its Update branch button to
regenerate. No freshness checks are bypassed. Re-run regeneration after *every*
merge, even if Git reported no conflicts.

Help-size tests gate each command's JSON (12,000 characters maximum) and the
average (3,000), rather than a global total that every new command changes.
The JSON and text-index totals are reported as test properties, not gates;
the text index's existing per-line width check still catches bloated entries.
The compact discovery index has a separate 60,000-character ceiling and a
20,000-token approximation ceiling (word pieces plus JSON punctuation,
without downloading a tokenizer model). Agents read this index first, then
`wgsextract help <command> --json` for full schemas.

The CLI help document is not the server protocol. HTTP discovery uses
`GET /v1/ops` (summaries) then `GET /v1/ops/{op}` (full Params schema);
MCP `tools/list` must include its protocol-required `inputSchema`. Both use
the same typed Params definitions as per-command CLI help, not the compact
help index, so their invocation schemas remain complete.

Server response envelopes are writer-used TypedDicts in `server/transport.py`.
`scripts/gen_server_contracts.py` generates the committed response JSON Schema
bundle, OpenAPI 3.1 document and raw GUI TypeScript transport declarations;
`--check` detects drift, and `just regen` updates all three. Keep GUI presentation
types and tolerant parsing adapters handwritten: they are not wire documents.
Loaded result/report documents remain opaque in the server spec until their
own writer models drive their public schemas; a schema ID check is not full
document validation. Job projections still omit unset fields and compact
results still omit provenance and empty values.

The three read-only lookup inputs live in `server/lookup_inputs.py`. Their
frozen dataclasses and annotated constraints drive both HTTP/MCP discovery
schemas and request validation, and lookup readers consume the resulting typed
objects. This deliberately supports strings, booleans, integers and string
arrays; unsupported annotations fail closed. Optional `None` defaults represent
omitted fields internally: explicitly supplied JSON null is still rejected.
This input-specific adapter keeps its constructor/omission semantics separate
from the output-schema generator's complete dataclass projections.

The "Start from what you have" paths in both come from `JOURNEYS` in
`registry.py`. To retire a command, don't delete it: add it to `DEPRECATIONS`
there with its replacement and the next minor version as `remove_in`, and add
it under `## Deprecated` in a release-note fragment in
`docs/release-notes/unreleased/` (see its README). A deprecated
command keeps working, warns `deprecated_command` when run, and is listed apart
in the help and docs.

### Running state and worker threads

What a running command reads (its result, progress callback, cancel token,
settings, where its tools' stdout goes) is on the `ExecutionContext` in
`core/execution_context.py`, a `ContextVar`, so concurrent library calls keep
apart. Reach it through `current_result()`, `report()`, `checkpoint()` and
`settings` as before. Start worker threads with `context_thread` or
`ContextThreadPoolExecutor`, which run them in the caller's context (a
future's done-callbacks in the context they were added in); ruff rejects a
bare `threading.Thread` or `ThreadPoolExecutor` in command code.

### Issue-report package

Issue-report internals live in `core/issue_report/`: `redaction.py` sanitizes
text, `runs.py` loads local run records and discovers sensitive names, and
`environment.py` collects installation diagnostics. The package initializer
assembles reports and fits GitHub links; existing `core.issue_report` and
`api.support` imports remain unchanged.

### Alignment pipeline

The `commands/align.py` command retains orchestration and existing job/helper
imports. Its `alignment/` package separates shared jobs/reference primitives
(`common.py`), native BWA/memory planning (`short_reads.py`), read/reference
preflight (`preparation.py`), Parabricks dispatch (`gpu.py`) and Ultima,
minimap2 and pbmm2 execution (`backends.py`). Existing read-source, read-group,
selection and output helpers remain in the same package.

### Realignment pipeline

The `commands/realign.py` command retains orchestration and existing job/helper
imports. Its `realignment/` package separates job/input qualification and FASTQ
discovery (`preparation.py`), memory/disk/time planning (`resources.py`), journaled
alignment/index publication (`mapping.py`), single-pass/chunked long-read execution
(`long_reads.py`) and extraction/streaming/resumption (`steps.py`). The resumable
workflow bodies remain intact; helper dependencies flow through these layers
without importing the realign facade.

### DeepVariant pipeline

The `commands/vcf/deepvariant.py` command keeps orchestration and its existing
helper import paths. Its `deepvariant_pipeline/` package separates model/image
selection (`models.py`), host/postprocess resource planning (`resources.py`) and
resumable stage execution (`stages.py`).

### Python module size ratchet

`pixi run lint` checks Python files under `src/`, `tests/` and `scripts/`.
New modules must stay at or below 800 physical lines. The desktop app (`gui/`)
keeps its own policy; generated run outputs, scratch work and environments are
not scanned.

Existing oversized files have fixed, reviewable ceilings in
`scripts/python_size_baseline.json`; exceeding one fails with
`python_size_exceeded`. Split responsibilities rather than add or raise an
exemption. The initial ceilings include the bounded positive growth already
present in pinned, open pre-policy PRs, so introducing the guard does not break
those in-flight changes.

After shortening, splitting or deleting an outlier, run
`pixi run python scripts/check_python_size.py --refresh` and commit the reduced
baseline. Refresh only lowers/removes existing ceilings: it cannot grandfather
a new megafile or increase a budget, and refuses to write while a new/growing
module violates its ceiling. Removed files require their obsolete entries to be
removed (`python_size_stale_baseline`); remove only that entry if other budgets
still reserve in-flight work. Global refresh also removes exemptions for files
reduced to 800 lines or less.
Wait for reserved pre-policy versions to land before globally refreshing their
budgets; individual ceilings may be edited downward in the meantime.

The same guard runs in pre-commit, PR/main lint and exact-head generic
certification. It uses no network, extra dependencies or environment settings.
For a separate checkout, use the run-specific `--root`/`--baseline` arguments.

## Write tests

A good test fails when the behaviour a user relies on breaks, and survives a refactor that keeps that behaviour.

Large DeepVariant, native-aligner, realign, personal-report and server/path suites
are divided into focused `test_<family>_<topic>.py` modules.
Their original `test_<family>.py` modules
keep shared helpers and function-scoped fixtures, so existing imports and fixture
behavior remain stable. New integration modules must retain their CI tier.
Imported fixture aliases preserve autouse behavior too: report siblings retain
`served_lists` so their gene-list downloads stay isolated behind the fake network.
Modules using the shared `out/fake_30x` fixtures also retain a direct
`ensure_fake_data` import: the loadgroup scheduler uses it to keep shared-data
generation on one worker, even when the fixture itself is imported.
The standalone `test_server_shared_data.py` guard names its source inputs with
explicit repository-relative paths, so affected-test selection runs it whenever
those consumer modules change.
Its companion `test_server_shared_data_routing.py` checks that selection and runs
when the worker guard changes; both guards stay outside the CI-only test list.

CI routing and workflow-policy tests also have focused landing/required-gate and
readiness/path-filter siblings. Original modules retain shared helper owners;
all siblings stay in the CI-only and documentation selections, so splitting
tests never removes their checks from readiness or certification.

- **Assert observable behaviour.** That means:
  - output files and their content
  - exit codes 0/1/2/3
  - the result manifest
  - structured warnings
  - documented CLI and API behaviour
- **Use real tools on tiny data.** Prefer the fake genomes and fixtures with real samtools and bcftools. The helpers live in `tests/htslib_helpers.py`, `tests/fake_tools.py` and `tests/smoke_utils.py`. For Pixi environments, the `fake_pixi` fixture runs a stand-in pixi with fake environments in a temporary folder. For disk-space preflights, `tests/disk_volumes.Volumes` puts folders on fake volumes with a set amount of free space and makes inputs look bigger than they are.
- **Mock only true boundaries:** network, containers, GPU, very slow tools, the OS or platform, and the clock. Don't patch private helpers (`_name`) or the command runner to record what would have run.
- **Assert argv only where the flags are the scientific contract.** For example, a caller's ploidy or a minimum mapping quality. Assert just those flags, with a `# contract: <why>` comment.
- **Check message codes or key facts, not whole prose.** Help text is checked once, by the generated-help and doc tests.
- **Name the bug in each regression test** (`#123` or a short description), and check the symptom the user saw.
- **Don't restate constants, defaults or re-exports.**

`pixi run test-smells` lists private-internal patches and whole-argv asserts in test lines you added since `origin/main`. CI shows the same list as warnings on the PR. It is advisory and never fails.

## Make a change with an AI agent

The project is developed with AI coding agents. [AGENTS.md](../AGENTS.md) holds the rules they follow: test and run what they write, run ruff and mypy, keep outputs in `out/` or `tmp/`, keep defaults unchanged.

1. Install an agent, for example GitHub Copilot CLI (`npm install -g @github/copilot`), and start it in the repository: `copilot`.
2. Pick an issue from the [good first issue](https://github.com/WGSExtract/wgsextract-cli/labels/good%20first%20issue) list and tell the agent the outcome, how to check it, and where to stop. For example:

   > Fix issue #N. Add tests that fail without the change, run `just check`, then open a pull request that closes the issue.

3. Review what it did: read the diff (`/diff` in Copilot CLI, or `git diff`), and check that the tests would fail without the change. Run the command yourself on real data when you have it.
4. Have it push and open the pull request, then watch CI (`gh pr checks --watch`). Paste failures or review comments back to the agent until it's green.

   A job that fails with **Upstream package mirror unreachable** couldn't reach Ubuntu's package mirrors; that is an outage, not your change. Ubuntu packages come through `.github/actions/apt-install`, which tries several official mirrors, gives up within minutes, and installs from its cache when it has one. `rerun_lost_runners.yml` reruns such jobs up to twice, normally recording each request in the current week's pinned **CI flake ledger**. Audit outages are logged without blocking runner recovery. It also recovers lost runners, reports confirmed flaky tests as bugs, and blocks retries matching red main. See [CI failure observation and retries](ci_failures.md) for the policy and the audited landing-script command.

Keep each change small and state how you'll verify it. Most problems show up when the result is checked against real data, not in review.

## Release a change

Merged changes ship in the next release; see [releasing.md](releasing.md).
