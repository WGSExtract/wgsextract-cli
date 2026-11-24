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
genomic jobs cache public hs38. Action updates retain full commit SHAs and
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
- Run `pixi run gen-command-docs` to regenerate `docs/commands.md` and the
  command cards on the site's CLI page (`site_src/cli.md`), plus the bundled
  `assets/cli/help-index.json` used for fast, configuration-independent discovery.

`tests/test_cli_help.py` fails until all three are done.

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
help. It updates the API snapshot, `docs/api.md`, `docs/commands.md`, the bundled
compact discovery index, and the generated command cards in `site_src/cli.md`.
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

The setup scripts configure the `merge=generated` driver in `.gitattributes`.
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

## Retire stale agent worktrees

The post-work cleanup checklist does not schedule itself. On a Linux host, run
these commands from the primary checkout on `main`:

```sh
python3 scripts/cleanup_worktrees.py                   # offline audit, no removals
python3 scripts/cleanup_worktrees.py --github          # verify eligibility only
python3 scripts/cleanup_worktrees.py --github --apply  # retire eligible worktrees
python3 scripts/cleanup_worktrees.py --github --trim-caches  # preview cache trimming too
```

`--github` uses an existing authenticated `gh` session to request PR metadata
from github.com. Branch names and worktree contents are matched locally, never
uploaded. A candidate must live directly under `scratch/`, and exactly match
the head of its latest PR from this repository, merged into `main` at least a
day ago and authored by the authenticated account. Git removes the worktree
without `--force`; the branch stays. An authentication or state error stops the
run. The private JSON report in `out/worktree-cleanup/` records skips, removals
and errors.

Dirty, locked, detached, active or linked worktrees are left alone. Activity is
checked through Linux `/proc` across process UIDs. If Linux hides or denies
inspection of a process, the run reports the error and does not remove
unverified worktrees. Use the optional inspector below when unprivileged
process visibility is insufficient. Use `git worktree lock` before starting
work in a checkout that might be eligible for cleanup.
Do not start work in an eligible checkout during cleanup. Symlinks are checked
in this repository and its registered worktrees, including the primary
checkout's Git hooks, including custom `core.hooksPath` directories. If
any `hook.<name>.command` is configured, cleanup retains every candidate
and reports the hook key: arbitrary hook commands can depend on paths that
cleanup cannot verify. Lock a worktree if a hook script in a hooks directory
or another project depends on its files.
Nothing manages your reference library.
Samples and other unknown files block removal. With `--trim-caches --apply`, the same
PR and activity checks allow trimming verified generated Pixi, Ruff, mypy and
pytest caches in retained worktrees. Unrecognized cache contents, unlocked
Conda packages and links to a cache block trimming that cache. Pixi package
files must match local installed Conda or wheel checksums; missing or
mismatched checksums retain the environment without fetching metadata.
Only the named cache directories are deleted; the checkout, branch and other
files stay.
Python bytecode blocks worktree retirement unless it can be reproduced from
tracked or archived source. Unlisted bytecode in Pixi environments must match
bytecode regenerated from verified installed source. A matching header is
not enough; caches from another Python version or a source-rewriting tool
such as pytest may therefore be retained.
The report distinguishes cache trimming from worktree retirement.
Small UTF-8 logs under `out/` and recognised Python, shell, Lua
and R source files under `scratch/` (1 MiB per file, 32 MiB total) are copied
to a private, local directory in `out/archive/` without following links. They
are not executed. Do not put secrets in run logs or scratch sources.

### Install the read-only activity inspector (Linux administrator)

Linux can hide the cwd, mappings and file descriptors of services and login
processes even from other processes of the same UID. The optional inspector
reads this state across process UIDs without granting the cleanup command or Sheprd any elevated
permissions. It accepts only inode-verified registered worktree names in one
repository, checks the local Unix peer UID, and returns matching numeric PIDs
or an unverifiable result, never process paths or arguments. The service
cannot run Git, remove a checkout, or run caller-supplied commands. If a
process's mount namespace or view of the root cannot be safely compared, it
does not certify inactivity. A private root or masked `/home` alone does not
prove inactivity: the inspector still checks retained descriptors, mappings,
working directories and invocation paths, and rejects visible mount aliases.

From the separately reviewed revision, as the ordinary repository owner,
stage the exact committed blobs and prepare local settings under `out/`.
Verify `pwd -P` is the registered **primary** checkout and `id -u` is its
non-root owner:

```sh
reviewed=FULL_REVIEWED_COMMIT_SHA
mkdir -p out/worktree-inspector-install
git show "$reviewed:scripts/worktree_activity_inspector.py" \
  > out/worktree-inspector-install/inspector.py
for unit in wgse-worktree-inspector.service wgse-worktree-inspector.socket; do
  git show "$reviewed:scripts/worktree-inspector/$unit" \
    > "out/worktree-inspector-install/$unit" || break
done
printf '[Socket]\nSocketUser=%s\n' "$(id -u)" \
  > out/worktree-inspector-install/owner.conf
```

Create `out/worktree-inspector-install/config.toml` with these two entries,
using the actual primary canonical path and numeric owner UID:

```toml
repository = "/home/OWNER/src/wgsextract-cli"
uid = 1000
```

An administrator explicitly installs the reviewed files; no worktree script
is executed as root. The example uses an existing authorized `sudo-session`
grant. On updates, stop the socket and service before replacing their files.
Use systemd and system Python 3.11 or newer. Stop on any failure; verify the
installed service and socket against the reviewed commit and inspector
SHA-256 against `INSPECTOR_SHA256` in the reviewed `scripts/cleanup_worktrees.py`
**before** enabling the socket.

```sh
sudo-session run -- /usr/bin/install -d -m 0755 \
  /usr/local/lib/wgse-worktree-inspector /etc/wgse-worktree-inspector \
  /etc/systemd/system/wgse-worktree-inspector.socket.d
sudo-session run -- /usr/bin/install -m 0644 \
  out/worktree-inspector-install/inspector.py /usr/local/lib/wgse-worktree-inspector/inspector.py
sudo-session run -- /usr/bin/install -m 0644 \
  out/worktree-inspector-install/wgse-worktree-inspector.service \
  out/worktree-inspector-install/wgse-worktree-inspector.socket /etc/systemd/system/
sudo-session run -- /usr/bin/install -m 0644 out/worktree-inspector-install/config.toml \
  /etc/wgse-worktree-inspector/config.toml
sudo-session run -- /usr/bin/install -m 0644 out/worktree-inspector-install/owner.conf \
  /etc/systemd/system/wgse-worktree-inspector.socket.d/owner.conf
/usr/bin/sha256sum /usr/local/lib/wgse-worktree-inspector/inspector.py
git show "$reviewed:scripts/worktree-inspector/wgse-worktree-inspector.service" \
  | cmp - /etc/systemd/system/wgse-worktree-inspector.service
git show "$reviewed:scripts/worktree-inspector/wgse-worktree-inspector.socket" \
  | cmp - /etc/systemd/system/wgse-worktree-inspector.socket
sudo-session run -- /usr/bin/systemd-analyze verify \
  wgse-worktree-inspector.service wgse-worktree-inspector.socket
sudo-session run -- /usr/bin/systemctl daemon-reload
sudo-session run -- /usr/bin/systemctl enable --now wgse-worktree-inspector.socket
```

Before enabling any cleanup schedule, run
`python3 scripts/cleanup_worktrees.py --github --inspector` from the primary
checkout **without** `--apply`. Verify it identifies a live test process as
busy and reports a clear, otherwise eligible worktree accurately. When the
socket is unavailable, unverifiable, or serving a different inspector digest,
`--inspector` never falls back or removes worktrees. Local inspection checks
each live Linux task and rejects external bind mounts of the checkout or its
contents. It stops if any task's filesystem view or references cannot be
verified; use the installed inspector on such hosts. To suspend inspection,
disable the socket and stop the service; pause the Sheprd schedule as well.

### Approve the daily cleanup schedule

Start the Sheprd automation service on the host, register the primary
checkout, and inspect the plan. The scheduled command uses the installed
inspector and `agent-coord` for its worktree-cleanup lock and resource limits:

```sh
/usr/bin/sheprd-automate repo add "$PWD"
/usr/bin/sheprd-automate check .herdr/automations/stale-worktrees.luau
/usr/bin/sheprd automation approve .herdr/automations/stale-worktrees.luau
```

The owner must approve the plan; agents must not approve it. The scheduled run
uses GitHub explicitly, runs no agents, and reports success or failure in
Sheprd. It retires eligible worktrees and trims verified caches in retained
worktrees, without pulling `main` or installing anything. Update the
inventory module's SHA-256 in the janitor if that module changes. After any
janitor change, update its SHA-256 in the automation and have the owner
approve the new plan before resuming scheduled cleanup. An
inspector upgrade requires a separately reviewed administrator install as
well as updated digests before scheduling resumes.

## Release a change

Merged changes ship in the next release; see [releasing.md](releasing.md).
