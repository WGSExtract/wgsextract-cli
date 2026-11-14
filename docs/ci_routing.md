# CI routing and cache budget

PR area checks diffs the PR's **base...head**, not just the latest commit.
`--no-renames` includes deletions and both sides of moves. Unknown paths run
the full CLI matrix; mixed PRs run the union of their areas.

Drafts retain the documentation route and run lint/routing-policy tests.
When the diff selects CLI tests, they also run the lightweight product subset
(`test_microarray_vendor_formats.py`, `test_table_writer.py`), without a
bioinformatics environment. The OS matrix and Install Lab wait until
`ready_for_review`; readiness recomputes the complete diff.

Test dependencies take precedence over area exclusions. The router scans the
root test suite's imports, path literals, composed pathlib paths and directory
globs without executing tests. Inputs read only by the lightweight docs modules
select that pytest job; other referenced inputs select the full CLI matrix.
The map is deliberately conservative, including some fixture-data literals.
Every Python change, including native and GUI/site Python, selects CLI pytest
and explicit changed-file lint. CLI Python retains root Ruff/format rules and
the existing strict CLI mypy gate; GUI Python retains its own compile gate and
adds fatal-error Ruff checks without imposing unrelated root formatting.

| PR changes | Checks selected (in addition to routing and the secret scan) | Estimated runner-minutes/push |
|---|---|---|
| Markdown, including `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, native READMEs | Documentation freshness | 1–3 (previously ~125) |
| CLI source/tests | Four-OS CLI matrix, docs, CodeQL | ~90–100 (previously ~125) |
| Installer, packaging, Pixi lock/manifest, CLI entry points | Above plus Install Lab and applicable package checks | ~125; no intended coverage reduction |
| Native source only | Native build/ctest/package workflows and CLI help smoke | Native build cost + <1; removes ~125 of unrelated checks |
| Site sources or `gui/mockups/` only | Pages checks plus pytest for referenced inputs | Usually 1–3; Python/other CLI test inputs select the matrix |
| App source | Existing GUI checks; app packaging also selects Install Lab | Depends on the app area |

These are estimates from the pre-routing workflow medians, not billing claims.
Native build/package time depends on architecture and CUDA compilation; routing
does not promise to make those builds faster. The overall 40% weekly reduction
depends on the proportion of docs/site/native PR pushes and must be measured
after deployment.

## Coverage

The lightweight documentation job installs Python dependencies, not bioinformatics
tools. It runs:

- `tests/test_api.py::test_api_docs_are_up_to_date` (`docs/api.md`);
- `tests/test_api_cli_parity.py` (the CLI structure/help snapshot);
- `tests/test_cli_help.py` (`docs/commands.md`, generated site cards and help
  topics/examples);
- `tests/test_bump_version.py` (CLI/app site sources, version/distribution docs);
- `tests/test_regen.py` (real Git conflicts and regeneration/freshness checks,
  without bioinformatics tools);
- `tests/test_build_mockup_previews.py` (static branch preview publishing);
- routing/action-policy tests, including a meta-gate that every test-referenced
  input selects pytest and every light-only input's readers run in that job;
- `scripts/validate_site_sources.py` (existing source/front-matter validation).

`DOCS_TEST_TARGETS` in `scripts/ci_test_dependencies.py` is the single source for
the job's pytest selection and its coverage classification. For example,
`site_src/cli.md` cannot bypass the generated-card freshness assertion, and
`native/bench/power.py` runs its unit tests through CLI pytest.

There is no existing general Markdown link/anchor checker. Pages source checks
also run both site builders and the app's site-source validator, without
deployment permissions. The secret scan remains on **all PRs and queue groups**,
including docs: routing must not hide a secret in prose.

The OS workflows and Install Lab run unconditionally on main pushes and nightly,
as do the GUI and native workflows covered by area routing. CodeQL retains
unfiltered main coverage and runs nightly; PRs scan code/configuration paths
only. Releases and manual dispatches remain available. Install Lab can be
forced on any PR with the `run-install-lab` label. Only this label is actionable:
adding it runs Install Lab without repeating the matrix; other labels are ignored.
Label runs cannot cancel a code-validation run. Routing, documentation, lint
and summary label checks have distinct names. Draft-fast, native smoke and
Pages checks use static names so skipped jobs do not expose raw name expressions.
The landing validator scopes area checks to the selected ready run.
Its nightly run includes the former weekly negative
controls and pinned-release scenarios.

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
- Generated chrM and whole-genome end-to-end tests still run on every OS
  (after the integration tier on x64). The separately configured imputation,
  ONT and Windows vep-rs checks remain in place.

Both tiers run on every CLI-routed PR, not only main: the existing conservative
area router includes all Python, tool/configuration and test-input changes.
Both run unconditionally on main and nightly too. ARM64 retains its complete
suite in one job. No slow suite is demoted to nightly-only coverage.
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
It has the same CLI-PR routing and unconditional main/nightly coverage. Neither
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
verified with a docs-only draft PR against the implementation branch before
the routing itself lands. Other main-only workflow triggers are unchanged.

## Landing checks

`main` lands through a merge queue whose only required status check is
`required`, a dependency summary in PR area checks. It runs for every PR, docs-only
included, and for every merge-queue group. It passes when routing succeeded
and every required job passed or was skipped by area routing; a failed or
cancelled job fails it. `always()` keeps it reporting when a dependency fails,
because GitHub counts a skipped required check as passing.

| Event | Jobs `required` waits for | Not required |
|---|---|---|
| Pull request | routing, draft lint/fast tests when applicable, documentation freshness, changed Python lint, CI Ubuntu (lint, tests, imputation, ONT), secret scan, native CLI smoke, Pages checks | macOS, Windows, ARM64, package smoke, Install Lab, CodeQL, GUI and native workflows: advisory |
| Merge-queue group | the same set, routed by the group's diff against `main` | not run |

The required set stays on fast Ubuntu jobs. The other OS jobs, which more
often lose runners or wait for capacity, stay advisory so they can't stall the
queue; main pushes and nightly runs still run them in full. A job-level `needs:` summary in one workflow, rather than polling the
checks API, reports exactly once per run with no extra permissions or timeout.
The queue's `merge_group` events ignore `paths:` filters, so routing uses the
group's `base_sha...head_sha` diff, keeping docs-only groups to a few minutes.

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
in the selected area run or any other workflow. Stale area checks from earlier
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
