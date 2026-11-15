# CI routing and cache budget

PR area checks diffs the PR's **base...head**, not just the latest commit.
`--no-renames` includes deletions and both sides of moves. Unknown paths run
the full CLI matrix; mixed PRs run the union of their areas.

Drafts run **no CI runner jobs**, including documentation and secret checks.
Before `gh pr ready`, run `pixi run lint`, the routing/workflow-policy tests,
relevant product tests and type checks, documentation freshness checks if docs
changed, and `pixi run pre-commit run gitleaks --all-files` locally. The former
draft product subset was `test_microarray_vendor_formats.py` and
`test_table_writer.py`; use relevant tests rather than relying on remote draft
validation. `ready_for_review` recomputes the complete diff and runs the same
area-selected checks as a ready PR push.

GitHub Actions has no workflow-level `if`. Job-level draft conditions are
evaluated before runner allocation (and before expanding a matrix); reusable
workflow callers have the same gate. Skipped workflow records may appear, but
use no runner. Every PR workflow includes `ready_for_review`.
The shared `os_tests.yml` suite also gates its matrix job on the inherited
draft payload, in addition to the PR area's gates on its OS callers.
Workflows with cancelling concurrency retain a runner-free `converted_to_draft`
event in the same concurrency group to cancel in-flight ready runs. Pushes, schedules,
manual dispatches, workflow calls and merge groups retain their existing gates.
The runner-free `pr_draft_cancel.yml` workflow also cancels the separate
Install Lab label group on redrafting/readiness, without letting ordinary
label events cancel code validation.

Test dependencies take precedence over area exclusions. The router scans the
root test suite's imports, path literals, composed pathlib paths and directory
globs without executing tests. Inputs read only by the lightweight docs modules
select that pytest job; other referenced inputs select their affected test files.
The map is deliberately conservative, including some fixture-data literals.
Product Python changes, including native and GUI/site Python, select CLI pytest
and explicit changed-file lint. Known docs/CI generator scripts and lightweight
test modules select their own checks instead of the full CLI matrix.
CLI Python retains root Ruff/format rules and
the existing strict CLI mypy gate; GUI Python retains its own compile gate and
adds fatal-error Ruff checks without imposing unrelated root formatting.

| PR changes | Checks selected (in addition to routing and the secret scan) | Estimated runner-minutes/push |
|---|---|---|
| Any draft PR | None; validate locally before readiness | 0 |
| Hand-written Markdown, including `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, native READMEs | Only site-source validation if a site source changes; otherwise no docs job | <1 (previously ~125) |
| Generated API/command reference only | Its freshness check in the cheap docs lane | 1–3 |
| CLI source/tests | Affected test files on four OSes, input-selected freshness, CodeQL | Depends on graph reachability; full-suite fallback remains |
| `.github/**`, `scripts/ci_*.py` | actionlint and CI policy/routing/readiness; one-OS smoke for changed matrix workflow | Small checks, not the full CLI matrix |
| Installer, packaging, Pixi lock/manifest, CLI entry points | Above plus Install Lab and applicable package checks | ~125; no intended coverage reduction |
| Native source only | Native build/ctest/package workflows and CLI help smoke | Native build cost + <1; removes ~125 of unrelated checks |
| Site Markdown only | Site-source validation, plus command freshness for generated CLI cards | <1–3 |
| Site code/assets or `gui/mockups/` | Pages checks plus pytest for referenced inputs | Usually 1–3; non-light test inputs select the matrix |
| App source | Existing GUI checks; app packaging also selects Install Lab | Depends on the app area |

These are estimates from the pre-routing workflow medians, not billing claims.
Native build/package time depends on architecture and CUDA compilation; routing
does not promise to make those builds faster. The overall 40% weekly reduction
depends on the proportion of docs/site/native PR pushes and must be measured
after deployment.

## Coverage

### Per-test PR selection

`scripts/ci_select_tests.py` selects individual test files, not whole areas.
Its AST import graph follows transitive imports, relative imports,
`from package import submodule`, and package initializers. Literal
`command_handler` targets and the finite `CommandRegistration` registry from
#804 (lazy CLI startup) are resolved without importing application code.
Unresolved dynamic imports conservatively include their readers; if a CLI
entry-point test reaches one, selection fails open to `all`. Literal module
names used by subprocesses and patch targets also participate.
Applicable conftest modules are implicit imports of every test. Shared helper
imports therefore participate transitively too, including sibling test
modules used as helpers; their literal file inputs propagate to importers.

Tests are selected when they reach a changed Python module, directly change,
or read a changed path/glob recorded by `test_inputs`. Changes to conftest,
helpers, shared fixtures, the dependency manifests/lockfile and unknown paths
select `all`. Deleted Python modules also fail open. Eager package
initializers can legitimately connect many tests: this selector does not
pretend a lazy runtime dispatch is narrower than the static graph.
The current shared fixture helper imports the typed API, whose eager package
initializer connects almost all application modules. Those changes can
select the whole suite even without the explicit `all` fallback. Narrowing
that shared dependency requires a separate fixture/API refactor, not dropping
edges from this safety graph. Non-Python application resources currently fail open
because the test-input scanner does not inspect runtime resource consumers.
Explicitly declared generated documentation inputs retain their freshness lane.

The `changes` job publishes `tests` and per-OS selections as JSON test-file
arrays or `all`. The four OS wrappers forward these inputs to the shared
`os_tests.yml` workflow, which passes them through
`scripts/ci_run_tests.py` to `pixi run test-ci`; existing tiers, stable module
shards and Ubuntu-only partitions apply **within** that set. Empty selections
skip jobs, and empty tier/shard partitions exit successfully. Selections are
validated and passed as argv, never evaluated as shell text. Explicit paths
are filtered using the `test-ci` task's ignore rules so generated-genome and
real-data suites remain exclusively in their dedicated steps.

Non-Markdown `.github/**` and `scripts/ci_*.py` changes select actionlint and the CI
policy/routing/readiness regressions, not the CLI matrix or Install Lab.
`ci_tests` includes their actual transitive script/action readers as well as
the baseline policy checks; the CI job executes that complete selection.
Changing an OS matrix workflow additionally exercises **only that OS** in
one lightweight smoke job; changing shared `os_tests.yml` exercises all four
OS smoke jobs. Mixed product/CI changes union their coverage. Declared docs
freshness modules join a product selection before the docs lane is suppressed.
Extra generated-genome, imputation, ONT and Windows vep-rs checks run only
when their test files are affected. Main pushes, nightly/manual runs and
merge-queue Ubuntu validation retain full-suite coverage.

Local callers can use
`pixi run python scripts/ci_changes.py --base origin/main --head HEAD --print-tests`.
Stdout is only the JSON file array or `all`; no GitHub credentials or output
file is required. `select_tests(paths, repository=...)` remains importable.

Replay with `pixi run python scripts/ci_select_replay.py --limit 50 --output
out/914`. It records each merged PR's files, selected fraction, failed
head-run test names from Actions logs, unavailable logs and misses.
Runner-minute savings are a linear estimate against the documented
125-minute baseline, **not measured billing savings**; environment setup and
fixed jobs do not scale linearly. The replay uses the current graph and
latest PR head, not historical imports or every earlier push. Main/nightly
misses must become selector regressions rather than suppressed failures.

The lightweight documentation job installs the package, pytest and jsonschema
**only when its selected Python tests are nonempty**, never for prose alone.
`DOCS_TARGET_INPUTS` in `scripts/ci_test_dependencies.py`, complemented by exact
test-input reads, selects each target independently:

- `tests/test_api.py::test_api_docs_are_up_to_date` (`docs/api.md`);
- `tests/test_api_cli_parity.py` (the CLI structure/help snapshot);
- `tests/test_cli_help.py` (`docs/commands.md`, generated site cards and help
  topics/examples);
- `tests/test_bump_version.py` (CLI/app site sources, version/distribution docs);
- `tests/test_regen.py` (real Git conflicts and regeneration/freshness checks,
  without bioinformatics tools);
- `tests/test_build_mockup_previews.py` (static branch preview publishing);
- routing/action-policy tests on CI scripts/workflows and test changes, not
  arbitrary prose mentioned by routing fixtures;
- `scripts/validate_site_sources.py` (existing source/front-matter validation).

`DOCS_TEST_TARGETS` in `scripts/ci_test_dependencies.py` is the available target
inventory, not an unconditional test batch. The router outputs `docs_tests`
and `site_validation`. Prose outside `site_src/` and `gui/site_src/` selects
neither; site prose runs both source validators with system Python and no
package installation. This does not build Pages for a Markdown-only PR.
Version/changelog prose does not run release-script tests.

When the CLI lane is selected, `docs_tests` is empty: affected freshness checks
are collected through their imports and declared inputs in that selection.
Freshness therefore runs in one lane, not both. CI-only policy checks likewise
run in their dedicated job, not again in the documentation job.
The same routing/output contract applies to PR and merge-queue diffs, and
`required` still waits for the selected docs or CLI checks. For example,
`site_src/cli.md` cannot bypass the generated-card freshness assertion, and
`native/bench/power.py` runs its unit tests through CLI pytest.

There is no existing general Markdown link/anchor checker. Pages source checks
also run both site builders and the app's site-source validator, without
deployment permissions. The secret scan remains on **all ready PRs and queue groups**,
including docs: routing must not hide a secret in prose.

### Main-push prose filters

The four OS trigger wrappers (CI Ubuntu, macOS, Windows and ARM64), CLI smoke, Linux aligners/DeepVariant,
GATK/GKL on Windows, GUI Tauri installer and CodeQL use
`paths-ignore: ["**/*.md"]` on main pushes, with no generated-reference or
changelog exceptions. Mixed Markdown/code pushes still run normally.
`os_tests.yml` is reusable-only; it inherits the filtered wrappers' invocation
and cannot start independently on a push or PR.
Generated references legitimately change with code inputs; the PR/merge-queue
docs or CLI lane already checked freshness before `required` passed.
The secret scan remains unfiltered on every main push; Pages retains its
existing site-source filters.

Install Lab, GUI distribution/CI/developer setup and macOS aligners/DeepVariant
retain the area-scoped main-push filters from #904 (main-push coalescing),
including the final `!**/*.md` exclusion. Their PR triggers use the same
Markdown exclusion (Install Lab is invoked through PR area routing).
A Markdown-only main push therefore starts only gitleaks, plus Pages for
site sources; none of these six workflows or the OS suite starts.
See AGENTS.md, "Docs fast path", for the narrower direct-push permission:
policy files and generated/test-checked references still require PRs.

All standalone `pull_request` workflows exclude prose-only Markdown diffs
through ordered path exclusions or exact non-Markdown includes (Developer
setup and Linux bwa-mem3 parity). Only `pr_ci.yml` starts: its router retains input-selected documentation
freshness, gitleaks and `required`, including generated-reference checks.
Mixed diffs still select the union of applicable code areas. PR event types
and job-level draft gates are managed separately by #910 (draft PR CI gating).

Merge-group routing, nightly schedules, reusable calls, releases and manual
dispatches retain their coverage. Install Lab can be
forced on any PR with the `run-install-lab` label. Only this label is actionable:
adding it runs Install Lab without repeating the matrix; other labels are ignored.
Label runs cannot cancel a code-validation run. Routing, documentation, lint
and summary label checks have distinct names. The now-skipped draft-fast, native smoke and
Pages checks use static names so skipped jobs do not expose raw name expressions.
The landing validator scopes area checks to the selected ready run.
Its nightly run includes the former weekly negative controls and pinned-release
scenarios.

### Main-push macOS capacity

Main pushes supersede older main-push runs independently in Install Lab, GUI
distribution, CI macOS, CLI package/benchmark smoke, macOS Aligners,
macOS DeepVariant, GUI CI and GUI developer setup. Their concurrency groups
separate push, PR, nightly and manual events. Nightly/manual runs keep unique
run-ID groups and are not cancelled by pushes. Reusable workflows use literal
workflow-specific prefixes, not the caller's `github.workflow`; tagged GUI
distribution calls also keep unique, non-cancelling groups, including a release
caller with a push event. Release publishing and `app_run` artifact selection
are unchanged.
Main-push retries (`run_attempt > 1`) also use independent run-ID groups:
rerunning an old commit cannot cancel the newest first-attempt main push.

The eight workflows previously submitted **13–14 macOS jobs per main merge**:
CI macOS 3, smoke 2, Install Lab 2–3 (one extra if it builds the tree app),
GUI distribution 2, and one each for Aligners, DeepVariant, GUI CI and GUI
developer setup. A CLI-only merge outside installer/packaging paths now submits
**5** (CI macOS plus smoke); Markdown-only submits **0** macOS jobs after the
prose filters. Relevant app/native
merges still run the matching workflows. Coalescing limits each workflow's
main-push backlog to its newest run; it does not promise a fixed saving in
runner-minutes or a measured queue-wait target.

The failure observer treats a cancelled superseded main run as obsolete:
no retry, red-main update or flake exemption from its partial passes.
Cancelled runs do not replace the last conclusive main failure/success used
for PR comparisons, including an earlier failed attempt of a cancelled retry.
A relevant newer main push or nightly success still closes that workflow's
red-main bug. Nightly results refresh the baseline after fixes outside the push
route; unrelated path-filtered pushes do not falsely mark it green.
Manual/nightly retries remain independent and can report confirmed recoveries
even when another run of their event exists.

### CLI test time and tiers

The three x64 OS jobs already used `-n auto --dist loadgroup` (two workers on
Windows/Ubuntu, three on macOS in the November 13 baseline). They now run two
independent, disjoint pytest tiers in parallel:

- **Core:** every test not explicitly listed in `INTEGRATION_MODULES` in
  `scripts/ci_test_tiers.py`. New tests automatically join this tier. Two
  parallel core shards use a stable module-name hash; module fixtures are
  never split between shards.
- **Integration:** the realignment, analyze-workflow, benchmark,
  benchmark-module, pipeline-interoperability, contig-calling, microarray-reuse,
  native-aligner and DeepVariant modules listed there. Whole modules retain
  their shared fixtures and the existing shared-smoke scheduler.
- Affected generated chrM and whole-genome end-to-end tests run on each selected
  OS (after the integration tier on x64). Separately configured imputation,
  ONT and Windows vep-rs checks retain their environments when affected.

Both tiers partition the affected files on CLI-routed PRs; an empty partition
passes without testing unrelated files. Non-prose main pushes, merge-queue
Ubuntu and nightly runs retain full coverage. ARM64 runs its selection in one
job. No slow suite is demoted to nightly-only coverage.
Only `test_genotyping.py` (pure genotype parsing/repeat classification) is
Ubuntu-x64-only in CI. Real-tool genotyping commands remain cross-platform.
Ordinary local pytest still collects everything; the opt-in `--ci-tier` and
`--ci-skip-independent` and `--ci-shard` flags implement these CI-only partitions.

The first implementation PR run measured 573 s for Ubuntu core versus 439 s
for integration, so just two jobs still missed the 480 s target. Core is
therefore split once more rather than dropping tests. Its first-run JUnit
costs predict approximately 665/431 accumulated test-seconds for the two
shards (each still has two xdist workers). This adds one environment/setup per
x64 OS; it trades some setup runner-minutes for latency, not free capacity.
Only core-1 seeds shared main caches and runs one-off core checks.

Per-test timing XML and the slowest 60 durations remain available on **every**
run, separately named by OS/tier. Non-gating line coverage is collected on
main, nightly and manual runs, not PRs; its per-tier reports describe the
selected tier rather than a merged whole-suite percentage.

`CLI package and benchmark smoke` runs the existing package-smoke and real-data
smoke benchmark commands as independent parallel jobs on all three x64 OSes.
It has the same CLI-PR routing, non-Markdown main coverage and nightly coverage. Neither
command precedes pytest anymore; neither has been removed. The failure observer
watches direct smoke runs and compares routed PR smoke failures with that
workflow's main run, as it does for the OS workflows.
`tests/test_smoke_real_full.py` follows the benchmark in that same job: its
real-data fixture discovers the benchmark's prepared HG00096 inputs. Moving
the benchmark without this suite would silently skip six previously passing
Ubuntu real-tool tests. The suite is excluded from `test-ci`, requires its real
inputs in CI (`--ci-require-real-data`), and publishes separate per-OS timing
XML. Optional tool/cache-specific cases retain their existing skip rules.

Test preparation also avoids duplicate real-tool work. Tiny FASTA/faidx and
SAM/sorted-BAM/index inputs prepared by `tests/htslib_helpers.py` are shared
across workers/sessions in ignored `out/test-fixtures/`, keyed by input content,
builder source, lockfile and tool binary. Tests receive copies, never shared writable
inputs. Locking and a completion marker prevent partially built cache hits.
Complete entries need no lock; Pixi launch-command wrappers bypass caching and
still prepare inputs with the real tools.
This is a local content-addressed cache, not another Actions cache family.
Large metadata-only stand-ins explicitly request sparse allocation on Windows;
plain `truncate()` otherwise allocated up to 64 GiB on NTFS. Their logical sizes
and memory-estimator assertions are unchanged.

The latest pre-change main runs reported these pytest durations:

| OS | Main run | Test step before |
|---|---|---|
| Windows | `43443418452` | 1,916.11 s (31m56s) |
| Ubuntu | `43443418355` | 1,076.50 s (17m56s) |
| macOS | `43443418134` | 1,413.57 s (23m33s) |

These are individual baselines, not 20-run medians. The targets in
#761 (reducing CLI test-step time) require PR measurements and a 20-run median;
the implementation PR reports the observed sample and any remaining gap.

PR area checks also accepts non-main target branches, so its routing can be
verified with a docs-only ready PR against the implementation branch before
the routing itself lands. Other main-only workflow triggers are unchanged.

## Landing checks

`main` lands through a merge queue whose only required status check is
`required`, a dependency summary in PR area checks. It runs for every ready PR, docs-only
included, and for every merge-queue group. It passes when routing succeeded
and every required job passed or was skipped by area routing; a failed or
cancelled job fails it in an otherwise active run. `!cancelled()` keeps it
reporting after failed dependencies while allowing whole-run cancellation to
stop the summary, compatible with #897 (cancellation-safe required gate).
Drafts cannot enter the queue and need no `required` result.
The landing validator rejects cancelled runs because GitHub counts a skipped
required check as passing.

| Event | Jobs `required` waits for | Not required |
|---|---|---|
| Ready pull request | routing, documentation freshness, changed Python lint, CI policy, selected CI Ubuntu checks, secret scan, native CLI smoke, Pages checks | macOS, Windows, ARM64, package smoke, Install Lab, CodeQL, GUI and native workflows: advisory |
| Merge-queue group | full Ubuntu CLI suite plus ancillary required checks selected by the group's diff | other OSes, package smoke and Install Lab not run |

The required set stays on fast Ubuntu jobs. The other OS jobs, which more
often lose runners or wait for capacity, stay advisory so they can't stall the
queue; main pushes run selected advisory workflows in full and nightly runs cover
all paths. A job-level `needs:` summary in one workflow, rather than polling the
checks API, reports exactly once per run with no extra permissions or timeout.
The queue's `merge_group` events ignore `paths:` filters, so routing uses the
group's `base_sha...head_sha` diff for ancillary lanes; Ubuntu retains full
test coverage even for docs-only groups.

Agents enqueue with `gh pr merge PR --squash --auto --match-head-commit SHA` after
every check, advisory ones included, is green, and don't update branches that
are merely BEHIND (see AGENTS.md, "Babysitting a PR"). Wait for checks with a
nonempty structured report rather than a fixed count:

```sh
set -o pipefail
gh pr checks "$PR" --watch --interval 90
gh pr checks "$PR" --json bucket,name,workflow,link | python scripts/pr_checks_ready.py --pr "$PR"
```

The validator requires a passing `Ready PR area checks` gate, `required` check
and `changes` job from the same workflow run, with draft-fast absent or skipped
in that run. That gate runs
only on non-draft, non-label events after all selected area jobs succeed.
Routing uses `set -euo pipefail`; the final gate fails on a routing failure
or a missing completion output, rather than accepting an all-skipped matrix.
It rejects old draft-only passes/skips (including the gap before the ready
run registers), empty reports, and pending, failed, cancelled or unknown statuses
in the selected area run or other reported workflows. Reported checks are
verified against their latest matching workflow runs; unrelated and superseded
draft/label runs do not block a passing ready run.
`--pr` is required: the validator reads the latest ready-for-review event and
current head from GitHub, then selects the newest non-draft area run for that
PR/head after the event and requires it to have completed successfully.
Run titles encode event action and
draft state so label-only runs are excluded. Earlier same-SHA ready runs cannot
satisfy a later ready transition; missing metadata and state changes fail closed.
Use `--repo OWNER/REPO` when running outside the repository checkout.
Verify the PR is non-draft, its head SHA has not changed, and check
reviews/threads and mergeability separately; enqueue with
`--squash --auto --match-head-commit`.
The readiness gate runs only on PRs before enqueueing, not on merge-group
commits. Queue runs need only the `required` set; their smaller matrix must not
be rejected for omitting the PR-only gate or advisory jobs.
The autopilot skill
documents this rule too. This validator does not itself merge anything.

## Cache plan

An API sample on 2026-11-13 reported **9.67 GB / 18 entries**; the immediately
following inventory had 24 entries (the API is not an atomic snapshot). Entries
were being evicted at the repository's 10 GB cap. Last-access times are available,
but the cache API does not expose historical hit rates. New shared cache actions
publish exact hit/miss and key in each job summary.

| Cache family | Before | After, per current lockfile (estimated) |
|---|---|---|
| Pixi | Many overlapping default/dev/optional environment sets (~8 GB in the sampled inventory) | Four CLI OS/architecture sets (~5.4 GB) + Linux lint (~0.1 GB) |
| npm | actions/setup-node could save PR-scoped duplicates | One download cache per OS/arch and package-lock hash (~0.1 GB total) |
| Cargo | Registry downloads plus a second Windows Tauri cache including `target/` | Shared registry/git downloads per OS/arch and Cargo.lock hash (~0.1 GB); no target cache |
| hs38 reference | Automatic writes on PRs | One main-owned verified public reference (~0.8 GB), read-only on PRs |
| liftOver chain | A new run-id key on each download | One checksum key (<0.001 GB), main-only writes |
| apt | PR cold misses could save duplicates | Main-only writes (~0.1–0.3 GB) |
| PharmCAT reference | Up to two infrequently reused ~1 GB OS-specific entries | Not cached; leave space for the common tool environments |

Target steady state is roughly **6.6–7 GB**, leaving headroom for CodeQL-managed
caches and lockfile rollover. This is a size plan, not an asserted measured
post-deployment size. Old keys age out after seven days unused; no active caches
are deleted by this PR. Monitor the API inventory after main seeds the new keys;
old and new keys can temporarily coexist.

Only each OS matrix job seeds its CLI Pixi cache; other jobs restore that same
set and install any missing optional environments without saving duplicate sets.
Pixi keys include the **whole lockfile hash**, pinned Pixi version, OS, architecture,
scope and workspace-path hash (Conda environments are not relocatable).
No prefix fallback restores an incompatible environment. npm and Cargo retain
only downloads, using their respective lockfile hashes. PRs never save these
caches, even on a cold miss.

The liftOver data cache retains a checksum-verified read fallback to old run-id
keys, so UCSC outages cannot break the migration. Main seeds the canonical
checksum-only key from either a verified fallback or a verified download;
no new run-id keys are written.

Every workflow installs Pixi through `.github/actions/setup-pixi`, which holds
CI's only Pixi version pin; `scripts/validate_github_actions.py` rejects direct
`prefix-dev/setup-pixi` steps. Pixi-binary-only build jobs pass `install:
"false"`, which also skips project caching: restoring an unused old environment
would keep it alive. Manual mirror/bwa qualification jobs and the Copilot setup
use the same read-only CLI cache rather than creating additional partial
environment sets.
Release verification installs its immutable tag's environment without caching;
an unpublished old tag can predate today's local CI helper actions, so Pixi
comes from the run's own commit before the tag is checked out.

The desktop app's workflows and the Copilot setup install its toolchain through
`.github/actions/setup-gui`: Node and `npm ci`, and on request Rust, Tauri's
Linux libraries or the CLI's Python dependencies. It holds CI's only Node pin;
the validator rejects direct `actions/setup-node` steps. `gui-build.yml` can
build an older ref, so it replaces that ref's `.github/actions` with the run's
own before using it. `gui-dev-setup.yml` deliberately bypasses it: it tests the
app's own bootstrap on runners without user tools.

CI Ubuntu, CI macOS, CI Windows and CI Linux ARM64 keep their own triggers,
concurrency groups and workflow names, but run the CLI suite through one
reusable workflow, `os_tests.yml`. Its `os` input selects the installer checks,
Windows imputation and vep-rs, and ARM64 checks; `reference: true` (Ubuntu and
macOS) adds the hg19ToHg38 chain and hs38 reference library. ARM64 passes one
`tier: all, shard: all` matrix entry, the `--ci-tier`/`--ci-shard` defaults, so
it collects the same tests as before. Its jobs report as `test-ubuntu / core-1`
on main and `ubuntu / test-ubuntu / core-1` in PR area checks; ARM64's single job is
`native-arm64 / native`. Each job uploads `tests-<os>-<label>`. The workflow has
no concurrency group of its own, so the OS calls in one run never cancel each
other.
