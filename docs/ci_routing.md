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
Install Lab shares the code-validation run and is cancelled with it on redrafting.

Draft-payload events other than `converted_to_draft` use a separate `-draft`
concurrency group. GitHub may deliver a draft `synchronize` event after
`ready_for_review`; its skipped jobs must not cancel the full ready run.
This isolation applies to every PR workflow with cancelling concurrency, not
just area routing. Ready pushes still supersede earlier ready runs, and
`converted_to_draft` deliberately enters the ready group to stop active work.
Draft summaries also use a different check name from `required`: GitHub accepts
skipped checks as passing, so a delayed draft suite must not satisfy the ready
run's merge requirement while its selected checks are still pending.
Non-PR groups are unchanged. No live-state lookup runner is needed: drafts
still allocate zero runners, and readiness always retains its own full run.

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

### Writer self-certification

Before marking a pushed owner PR ready, run `just certify` on a clean checkout.
On shared Linux hosts use
`agent-coord run --cores 4 --mem 8G --label "PR certification" -- just certify`.
`--platform-only` / `--generic-only` divide the layers across machines.
The host platform is detected, never overridden; Linux x64 cannot certify ARM64,
macOS or Windows. All selected tiers/shards run locally in full, without CI's
passed-test cache. Lint, strict typing, changed-file lint, routed docs/site checks
and the selector's complete affected CI-policy union form the generic layer.
Logs/JUnit live in `out/certify/<sha>/<layer>/`; successful exact-head statuses
include machine, test count, duration and the full log SHA-256.

Certification never provisions its execution environments, including inside the
nested pytest runner (the installer regression still exercises its own isolated
installation sandbox, exactly as CI does). Prepared default/dev environments are mandatory,
with vep-rs for Ubuntu/macOS, gatk/vep-rs for Windows, vep for Linux ARM64, and
the routed supplemental tools. Installed conda URLs/builds/checksums and Python
versions/RECORD integrity must match `pixi list --locked`. Retain each pinned
PyPI wheel as `out/certify/inputs/wheels/<sha256>.whl`: validation checks its
lock SHA-256 and compares installed files with the wheel, without downloading or
installing it. Missing archives refuse certification. Editable Python packages
must point to this checkout. Dependency updates require manually refreshed
environments and retained artifacts matching the new lock; stale versions or
changed wheel URLs/hashes cannot reuse old provenance. Both layers enforce this.
Ubuntu/macOS full suites also require the retained bootstrap archive at
`out/certify/inputs/wgsextract-reference-bootstrap.tar.gz` with its
pinned SHA-256, byte-matching installed bootstrap support files, the pinned hg19-to-hg38
chain and pinned hs38 genome. Retain the bootstrap archive separately when preparing
certification inputs: the normal bootstrap command removes it after extraction.
Missing or unprovable reference inputs refuse certification; no downloads occur.

The router executes only the verifier from the PR's **base SHA**, before checking
out PR code, with isolated Python. A missing base verifier or any change to the
verifier file falls back to hosted checks. Therefore the bootstrap PR adding the
verifier cannot itself skip hosted tests; its first trusted skip can happen only
after landing. Forks are excluded before the verifier executes. The verifier
accepts only the latest successful status on the exact same-repository head,
created by an account with write, maintain or admin permission. Missing
permissions and API errors fail closed. Writers can already push to main:
this is an attestation at the same trust boundary, not proof against a malicious
writer. CI needs `statuses: read` and the collaborator-permission API's
**Metadata: read** (implicit in `GITHUB_TOKEN`); local `gh` needs commit-status
write and PR-comment write access. No privileged token is given to PR code.

Generic classification rejects uncertain imports/fixtures, subprocess/tool use,
platform branches, concrete pathlib operations, permissions, signals, symlinks
and line-ending behavior. Currently no existing module is proven generic under
these deliberately strict rules; modules remain platform-sensitive until proven.
Uncertified generic tests run once on Ubuntu; main/nightly behavior is unchanged.
Gitleaks, CodeQL, Install Lab, native/app builds and package/benchmark smoke stay
hosted when routed. Merge groups never consume attestations. `required` and the
ready-gate accept certified skips only after successful routing, and the landing
validator relies on the current ready run rather than treating status contexts as
standalone workflow runs. Failed/pending certificates allow hosted fallback.

For macOS remotely, push the branch, acquire randy-mini's coordinator lock, and
SSH to a dedicated clean worktree with prepared Pixi environments. Leave 25 GiB
RAM free and run `just certify --platform-only` there using its existing `gh`
authentication; never forward credentials. Run Linux plus generic locally.
Whole-suite baseline savings are about 22 Linux runner-minutes plus 1–3 generic
minutes (package smoke remains), or about 33 additional minutes for macOS ARM64.
Sparse selected PRs save less.

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

### Test result cache

On top of the selection, `scripts/ci_test_cache.py` (#915, Bazel-style test
result cache) skips a PR's test files whose **exact dependency hash** already
passed. A test file's hash covers:

- every tracked file whose change would make `select_tests` pick it (its
  import closure, conftest dependencies and declared file inputs, evaluated
  one file at a time with the selector itself);
- every file whose change makes the selector fall back to `all` (`pixi.lock`,
  `pixi.toml`, `pyproject.toml`, runtime resources, shared fixtures and
  helpers, unknown paths);
- how tests run: `os_tests.yml`, the four OS callers, `setup-pixi`, the
  runner, tier and cache scripts;
- the job (OS, tier, shard, OS-independent skip), Python version, platform
  and runner image (`ImageOS`, `ImageVersion`). Coverage flags are excluded.

Equal hashes therefore mean the two trees differ only in files the selector
would not run that test for, so a cache skip is never less safe than #914's
selection (a regression test checks this for edits, additions and deletions).
Hashes only see files that exist; each recorded pass also keeps the names of
its tree's files whose deletion selects `all`, and a pass is reused only if
none of them has since been deleted.

Each `os_tests.yml` matrix job keeps one manifest of passed hashes:

| Event | Reads | Records after a passing run |
|---|---|---|
| Main push, nightly, manual dispatch on main | nothing (runs the full suite) | `test-pass-v1-<os>-<label>-main-<run>` |
| Ready PR push or `ready_for_review` | latest `-main-` and this PR's latest `-pr-` manifest | `test-pass-v1-<os>-<label>-pr-<run>` |
| Merge group, other refs | nothing (runs uncached) | nothing |

A lookup is exact set membership, so a stale, corrupt, evicted or missing
manifest can only re-run tests, never skip one whose inputs changed. A run
records only when pytest exits successfully (or finds nothing to collect in a
sparse partition); failed or cancelled runs record nothing. A cache entry is
saved only when it learned new hashes. Manifests keep 14 days, 4,000 hashes
and eight trees' names (tens of KB each), so they cannot crowd out Pixi caches.

**Security.** GitHub scopes caches by ref: a PR run writes only to
`refs/pull/<n>/merge`, readable by later runs of that PR alone, and can read the
base branch's (main's) caches. Only `push`, `schedule` and `workflow_dispatch`
runs can write main's scope. A PR therefore cannot poison what main or other
PRs read; at most it can skip its own tests, which its own workflow edits could
do anyway. Main, nightly and the merge queue run the full suite, so the
required gate never depends on the cache.

Each test job's summary lists the cache-skipped files, where they passed
(`main` or `pr`) and their hashes. To estimate savings, run
`pixi run python scripts/ci_cache_replay.py --limit 60 --junit <main pytest.xml...>`;
it replays consecutive ready runs of merged PRs.

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
forced on any PR with the `run-install-lab` label. Normal PR runs read this routing
label live from GitHub, rather than from a possibly delayed event payload.
Adding or removing labels does not start or cancel PR area checks: a skipped label
suite can shadow the passing `required` check and block the merge queue.
To apply a routing-label change immediately, toggle the PR to draft and then
ready (`gh pr ready --undo PR`, then `gh pr ready PR`); otherwise the next push
uses the current labels. The new ready run validates the complete diff, not just
Install Lab. Other labels do not affect routing.
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
Only the statically proven generic set is Ubuntu-x64-only in CI. Genotyping
modules whose import closure includes command/tool behavior remain cross-platform.
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
| Ready pull request | routing, documentation freshness, changed Python lint, CI policy, selected CI Ubuntu checks, secret scan, native CLI smoke, Pages checks, and the area gate covering selected macOS, Windows, ARM64, package smoke and Install Lab jobs | separate CodeQL, GUI and native workflows |
| Merge-queue group | full Ubuntu CLI suite plus ancillary required checks selected by the group's diff | other OSes, package smoke and Install Lab not run |

On PRs, `required` waits for the existing `ready-gate` verdict and requires it
to succeed, so enabling auto-merge cannot bypass a failing selected OS job.
Routing/selection skips and valid self-certifications remain accepted by that
gate. The area gate does not depend on `required`, avoiding a dependency cycle.
Merge groups skip the area gate and retain the same smaller required set;
main pushes run selected workflows in full and nightly runs cover all paths.
A job-level `needs:` summary in one workflow, rather than polling the
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

The validator requires a passing `required` check (which includes the area-gate
verdict on PRs) and `changes` job from the same workflow run, with draft-fast
absent or skipped in that run. The area gate runs
only on non-draft PR events after all selected area jobs finish.
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
| Test results (#915) | None | One small passed-hash manifest per OS test job, saved only when it learns new hashes |

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
