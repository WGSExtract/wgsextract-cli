# CI routing and cache budget

PR area checks diffs the PR's **base...head**, not just the latest commit.
`--no-renames` includes deletions and both sides of moves. Unknown paths run
the full CLI matrix; mixed PRs run the union of their areas.

When moving modules, audit workflow `paths` filters as well as the import graph
and ownership/review path lists. The remote-resource/real-chip concordance
workflow covers both remaining flat `core/microarray_*.py` modules and the
`core/microarray/**` package; Markdown exclusions still apply.

Remote-resource URL probes compare the resolved inventories at the PR's base
and head (including composed URLs and version changes), checking only added
URLs or changed mirror SHA-256/size pins. Code-only changes probe no unrelated
upstream hosts. Real-chip concordance retains its repository-hosted PR fixtures.
PRs changing `core/annotation_resources.py` also run the live SpliceAI format
and known-score tests. PRs changing `core/gene_lists.*` or the
`assets/report/gene_lists.*` manifests run the live gene-list download/pin tests;
changes to their corresponding live test modules select those tests too.
These behavioral checks are separate from URL probes: an unchanged URL does
not excuse validating a changed parser or resource contract.
Nightly runs check all upstream URLs (including restricted resources and
reference-genome alternatives), every mirror URL, and GitHub's published asset
SHA-256/size against the checked-in manifest. They also run the live format,
SpliceAI-score and gene-list checks. Byte-level verification runs at publication
and on every mirror download, not by downloading ~1 TB each night.
See [download mirrors and source rights](resource-mirrors.md) for the audit,
restricted-resource policy and manual publication/activation procedure.

Drafts run **no validation runner jobs**, including documentation and secret checks.
The API-only collaborator Copilot-review requester is the deliberate exception:
it can request a draft review on `opened`, skips the readiness request when
Copilot already reviewed the head, and never checks out or executes PR
code. The default-branch ruleset also reviews owner drafts when they are opened,
but not later pushes; agents request a fresh draft review after pushing
(`gh pr edit N --add-reviewer @copilot`) and handle its threads before
readiness (#1281). Readiness remains the point where validation starts, not the
first opportunity for review, so review rounds no longer rerun the matrix.
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
still allocate zero validation runners, and readiness always retains its own full run.

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
| Any draft PR | No validation; API-only review request for eligible collaborators | 0 validation minutes |
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

### Cheap optional-tool integrations (#1199)

`optional_tools.yml` runs exactly one Ubuntu x64 job for selected contamination,
FreeBayes, LongTR, modkit and ExpansionHunter-dependent tests. The PR router intersects the static
import graph's affected test files with these suites; unrelated changes allocate
no optional-tool runner. Merge groups use the diff's affected tests for this lane,
not the full OS safety selection, so docs-only and GUI-only queue entries do not
allocate an optional runner. Changes to this workflow, its PR caller/router,
runner, Ubuntu CI or the shared Pixi setup also exercise the integrations themselves. Drafts allocate no
runner. Main/nightly Ubuntu runs retain all five suites, subject to the existing
exact-head nightly guard. Manual dispatch of `optional_tools.yml` validates just
this small lane on a branch.
Before the new workflow reaches main, dispatch the existing `ubuntu_ci.yml`
with `optional-tools-only=true` on the branch instead. This explicit manual
override skips its normal jobs; default dispatches, PRs, main and schedules
retain their existing coverage.

The existing pinned Pixi action installs `dev verifybamid2 freebayes longtr modkit expansionhunter`
under the separate lockfile-keyed `optional-tools` cache family, seeded only from
main. Fixtures are synthetic: no reference bootstrap, full genome or sample
download is needed. Every selected tool must resolve before pytest; every JUnit
report must contain executed tests and zero skips/errors/failures. This lane does
not reuse passed-test hashes or platform certification, so a green OS suite with
optional-tool skips cannot substitute for real integration coverage. Both the PR
area verdict and merge-queue `required` check depend on it. The Ubuntu caller
delegates PR/queue coverage to the router, preventing duplicate Linux jobs.

FreeBayes runs its six caller cases, LongTR its tiny repeat suite, and modkit its
two BAM/CRAM parity cases. ExpansionHunter's prefix-gated hg19 catalog regression
uses its existing fake caller boundary; it is not a real caller qualification.
Other tests keep their existing OS coverage.
Measure hosted setup/test duration in the workflow run rather than assuming a
warm-cache cost. Large Clair3/model-backed native DeepVariant integrations and
real-data suites requiring external inputs remain separate follow-ups, not silent
new downloads.

### Setup-heavy checks and repeated nightly coverage (#1045)

PR installer checks and the Ubuntu app-bundle command test run only when the
complete diff touches their setup inputs: installers, packaging, GUI files,
Pixi/project manifests, CLI parser/registration, workflow/actions or Install
Lab/bootstrap scripts. Parser/registration inputs remain covered because they
can invalidate the app's bundled command lines.
The bundle check also retains its affected `test_cli_parser.py` trigger:
API wrappers and command-registry dependencies outside `cli/` can change the
bundled command contract even when installer setup is unrelated.
The same routing bit gates both macOS Install Lab jobs; Linux and Windows
Install Lab selection is unchanged. The Tauri installer keeps its input path
filter, including its Rust cache/build wrappers and root packaging/manifests.
Main and scheduled runs retain those checks whenever their workflow runs.
Thus a CLI edit unrelated to setup and the parser contract runs **zero** OS-installer steps
and **zero** Ubuntu app-bundle checks rather than four installer steps and one
app-bundle check. `run-install-lab` remains an explicit full-platform override.

The four full OS workflows and CLI smoke call `nightly_guard.yml`. Its only
runner is a small Ubuntu API check, allocated only on `schedule`; PRs,
pushes and dispatches skip it without a runner. A scheduled workflow skips its
expensive jobs only if its own successful **push** run exists for the exact
current main SHA in this repository. A different SHA, a failed/cancelled run,
a PR/nightly success, missing evidence or an API error retains full coverage.
Failure or timeout of the guard runner itself also runs coverage; a skip
requires both a successful guard and its explicit `skip=true` output.
Manual dispatch always runs. Green exact-HEAD evidence reduces ten nightly
runner jobs (four OS tests, Ubuntu lint/two supplemental jobs and three smoke
jobs) to five small Ubuntu guard jobs, with no scientific work repeated.
The `required` queue summary now directly depends on Windows as well as Ubuntu;
it cannot pass while code-selected Windows failed or was cancelled.
Queue Windows selection uses changed code/runtime-input paths, independently
of whether PR selection discovers consumers. A new unreferenced CLI module
therefore still receives the full Windows queue safety net.

### Conservative pure-move PR selection (#1045)

`scripts/ci_moves.py` inspects Git's complete base...head diff with 100% rename
detection. It uses the actual merge base and reads both Git blobs. The cheaper
path requires at least one exact rename and only Python files under
`src/wgsextract_cli/`; every other edit must have an identical AST after removing
`import` and `from` statements. Definitions, values, control flow and docstrings
must remain identical. Entry-point moves, changed assets/tests/configuration,
partial renames, parsing errors and uncertain module mappings retain normal
fail-open selection. Import-only edits without a move retain normal selection.

The narrow set contains tests whose import closure, including conftest/autouse
fixtures, reaches moved modules **or** modules with rewritten imports.
Unsafe importer consumers retain the normal selector's inclusion/fail-open
rules. Remaining old-name imports, deferred literal imports or renamed-away
file readers force normal selection instead of silently omitting consumers.
Whole-source recursive globs are not removed-path references when both sides
of a byte-identical rename remain inside the scan. Their scanner tests and
importing consumers still run; literal removed paths and narrower directory
globs retain the normal-selection fallback.
Before pytest, every selected OS imports the surviving changed modules and
collects the full suite; collection logs are uploaded even on failure. If no
test imports the moved modules, the existing tiny smoke pair still supplies
the runner for import/collection validation. The merge queue never uses this
optimization: full Linux and code-selected Windows remain its safety net,
with only their existing passed-on-main test-cache exclusions.
Writer platform certifications cannot skip these hosted move checks: their
current commands do not yet attest import/full-collection validation.

### Writer self-certification

#### One-time machine / checkout preparation

Use the Pixi version pinned in `.github/actions/setup-pixi/action.yml` (or a
compatible version exposing locked wheel URLs and SHA-256 values). Ensure `pixi`
is on PATH in non-interactive shells too; an unconfigured mise shim is not a
working Pixi installation. Install environments explicitly, just as CI does:

```sh
pixi install --locked -e default -e dev
just certify-setup
```

`certify-setup` downloads the exact PyPI wheels in `pixi list --locked`, checks
their lockfile SHA-256, retains them under `out/certify/inputs/wheels/`, and
validates the installed environments against them. It does not install tools,
rebuild dependencies from source, change the lockfile, or publish attestations.
Re-run it after lockfile updates and for each new checkout; Pixi environments
are non-relocatable and editable packages must refer to that checkout.
Already verified downloads are reused; incomplete or incorrect downloads never
replace a verified archive.

For **full Ubuntu/macOS product suites**, install the supplemental environments
needed by the selection (normally `vep-rs`; `imputation`, `pacbio`, `phasing`,
and `somalier` for their routed tests), then retain their wheels too:

```sh
pixi install --locked -e vep-rs
just certify-setup -e default -e dev -e vep-rs --reference
```

Repeated `-e` replaces the default environment list. `--reference` retains the
bootstrap release archive after verifying its pinned SHA-256, safely extracts
missing support files, fetches the pinned liftOver chain (with the same mirror
fallback as CI), and invokes CI's normal hs38 catalog installer. It then
validates all reference bytes, including the catalog-pinned full genome.
This can download several GiB; run under the resource coordinator on shared
hosts. Existing conflicting bootstrap files are not overwritten: the refusal
identifies the file to move aside before repeating setup.
An older or modified hs38 FASTA must likewise be moved aside with its indexes
before rerunning setup; the normal catalog installer reuses an existing FASTA
rather than replacing it with the pinned copy.

References use `reference_library` in config.toml, falling back to `reference/`.
To reuse a machine's existing library across worktrees, configure that path
using normal CLI configuration rather than copying the multi-GiB genome.
Each checkout still needs the retained bootstrap archive and wheel archives.
Generic-only and CI-policy-only certifications do not need reference setup.

#### Certifying a PR

Before marking a pushed owner PR ready, run `just certify` on a clean checkout.
On shared Linux hosts use
`agent-coord run --cores 4 --mem 8G --label "PR certification" -- just certify`.
`--platform-only` / `--generic-only` divide the layers across machines.
The host platform is detected, never overridden; Linux x64 cannot certify ARM64,
macOS or Windows. All selected tests run locally in full, without CI's
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
must point to this checkout. Dependency updates require explicitly refreshed
environments and `just certify-setup` artifacts matching the new lock; stale versions or
changed wheel URLs/hashes cannot reuse old provenance. Both layers enforce this.
Ubuntu/macOS full suites also require the retained bootstrap archive at
`out/certify/inputs/wgsextract-reference-bootstrap.tar.gz` with its
pinned SHA-256, byte-matching installed bootstrap support files, the pinned hg19-to-hg38
chain and pinned hs38 genome. Retain the bootstrap archive separately when preparing
certification inputs: the normal bootstrap command removes it after extraction.
Missing or unprovable reference inputs refuse certification with the missing
path and setup command; no downloads occur during certification.

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
Uncertified generic tests run once on Ubuntu; main retains full coverage.
Nightly may reuse successful exact-HEAD main evidence as described below.
Gitleaks, CodeQL, Install Lab, native/app builds and package/benchmark smoke stay
hosted when routed. Merge groups never consume attestations. `required` and the
ready-gate accept certified skips only after successful routing, and the landing
validator relies on the current ready run rather than treating status contexts as
standalone workflow runs. Failed/pending certificates allow hosted fallback.
Setup refusals, missing inputs and interruptions clear pending attestations with
`error` (“Not certified … hosted CI covers this SHA”), not `failure`. Only a
test runner report containing failed tests produces `failure`. Landing still
requires the passing hosted ready run; non-success local attestations never
make that otherwise-passing run fail the landing gate.

For macOS remotely, push the branch and run on lin-mid:

```sh
just certify --remote randy-mini
```

The controller enforces the same clean, pushed, same-repository open-PR and
writer checks as local certification. It acquires `agent-coord lock randy-mini`
for the entire remote operation and releases it even on refusal. A busy lock
refuses without disturbing its owner; retry only after the owner releases it.
Only the registered `randy-mini` SSH alias and public WGSExtract repository are
supported. SSH agent/port/environment forwarding is disabled; neither `gh` nor
a GitHub token is installed or sent to the Mac.

The remote bootstrap fetches the exact head and base SHAs over public HTTPS,
then creates/reuses `~/src/wgsextract-cli/scratch/certify-<head-sha>` as a
detached worktree. Branch names never enter a remote shell command. Prepare
that worktree's locked Pixi environments explicitly as above; missing tools
refuse rather than being installed. The worker runs `certify-setup` if retained
wheels or reference inputs need preparation, then uses the same macOS ARM64
selection and generic commands as local certification. `--platform-only` skips
the generic layer when it already ran locally; `--generic-only` runs only generic.
This explicit remote input-setup phase may download pinned archives; the test
phase still uses `--no-install`.

The Mac must start setup with 30 GiB disk free. A one-second watchdog stops the
worker and its child process group below 26 GiB, leaving headroom for the
required 25 GiB reserve. The controller sends five-second heartbeats; a lost
connection or 30 seconds without a heartbeat cancels the entire remote process
group, including setup and test children. SIGTERM follows the refusal/cleanup
path. A fresh SSH connection requests cancellation and confirms termination
before the controller releases the lock, including after transport failures.
If that confirmation fails the lock deliberately remains held: retry once SSH
is reachable, or confirm the matching control ID has a `.done` marker under
`~/src/wgsextract-cli/out/certify/remote-control/` before manually unlocking.
An abruptly killed controller (SIGKILL/power loss) likewise leaves its lock for
recovery; never break it while the remote supervisor may still be running.
No sudo, reboot, reverse SSH, or access to
`/Users/randy` is needed. The worker checks platform, clean tree and HEAD before
and after execution. Logs stream to lin-mid under
`out/certify/<sha>/remote-randy-mini/<layer>/run.log`; only lin-mid publishes
`local-ci/macos-arm64` and (if requested) `local-ci/generic`, with the machine
name and full streamed-log SHA-256. Missing/mismatched results, disk refusals,
SSH failures and interruptions produce `error`; verified failed-test reports
produce `failure`. Run Linux certification separately on lin-mid.
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
Ubuntu-only partitions apply **within** that set. Empty selections
skip jobs, and empty partitions exit successfully. Selections are
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
when their test files are affected. Merge-queue Ubuntu and code-selected
Windows run the full suite minus files the test cache already saw pass on main.
The queue plus main together run the full suite at every main SHA: a main
push runs only the files its merge group left to it (see "Main pushes after
the merge queue" below). Nightly and manual runs retain full-suite coverage.

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
| Main push (Ubuntu, Windows) | the files its merge group executed at this exact SHA, if one passed `required` | `test-pass-v1-<os>-<label>-main-<run>` |
| Main push (macOS, ARM64), nightly, manual dispatch on main | nothing (runs the full suite) | `test-pass-v1-<os>-<label>-main-<run>` |
| Ready PR push or `ready_for_review` | latest `-main-` and this PR's latest `-pr-` manifest | `test-pass-v1-<os>-<label>-pr-<run>` |
| Merge group | latest `-main-` manifest | the files it executed (not its cache skips), as artifact `test-pass-queue-<os>-<label>` |
| Other refs | nothing (runs uncached) | nothing |

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
do anyway. Merge groups read only main's manifest (owner decision,
2026-11-15): a skipped file's every tracked input matches a tree that passed
on main. The queue plus main together run every test file uncached at every
main SHA: a main push reruns exactly the files the queue skipped as cached, so an
undeclared input the hash misses fails the next main run instead of going
unnoticed. Nightly either runs the full suite or reuses that workflow's
successful main push at the exact same SHA, which carries the same guarantee.

Each test job's summary lists the cache-skipped files, where they passed
(`main`, `pr` or `merge-group-<run>`) and their hashes. Each job's
`tests-<os>-<label>` artifact also holds `test-scope.json`: the files run in that
job and, for each skipped file, where it passed. To estimate savings, run
`pixi run python scripts/ci_cache_replay.py --limit 60 --junit <main pytest.xml...>`;
it replays consecutive ready runs of merged PRs.

There is no existing general Markdown link/anchor checker. Pages source checks
also run both site builders and the app's site-source validator, without
deployment permissions. The secret scan remains on **all ready PRs and queue groups**,
including docs: routing must not hide a secret in prose.

### Main pushes after the merge queue

The queue lands the exact commit its merge group tested, so a main push usually
has the same SHA as a merge-group run that already passed. On 2026-11-21, 37 of
38 main SHAs did, and their CI Ubuntu and Windows runs took about 2,180
runner-minutes a day (#1373, merge-queue starvation). Main therefore doesn't
rerun what the queue just ran:

- A merge group's Ubuntu and Windows test jobs record the dependency hashes of
  the test files they **executed**, leaving out their cache skips, and upload
  them as artifact `test-pass-queue-<os>-<label>` (seven days).
- On a push to main, `scripts/ci_merge_group_passes.py` looks for a `pr_ci.yml`
  `merge_group` run in this repository whose head SHA is the pushed SHA, whose
  branch is under `gh-readonly-queue/main/` and whose latest `required` job
  succeeded. It downloads that run's manifest for the same OS and label.
- The main test job reads that manifest as one more cache source, never main's
  own manifest. A file is skipped only if its exact dependency hash, which
  includes the runner image, passed in that merge group. Main therefore runs
  exactly the files the queue skipped as cached, plus any whose hash differs.
  It records every file into main's manifest as before; the skipped ones carry
  the identical hash the queue passed.

If no passing merge group matches, or anything is uncertain (an API error, a
missing, expired or unreadable artifact), the manifest is not written and main
runs the full suite. The lookup fails open to more coverage, never less.
macOS and ARM64 don't run in the queue, so their main pushes keep the full
suite. Nightly, manual dispatch and other jobs in these workflows (lint,
imputation, ONT, optional tools, the e2e and Windows end-to-end steps) are
unchanged. Main's non-gating line coverage report covers only the files main
ran.

### Main-push prose filters

The four OS trigger wrappers (CI Ubuntu, macOS, Windows and ARM64), CLI smoke, Linux aligners/DeepVariant,
GATK/GKL on Windows, GUI Tauri installer and CodeQL use
`paths-ignore: ["**/*.md"]` on main pushes, with no generated-reference or
changelog exceptions. Mixed Markdown/code pushes still run normally.
`os_tests.yml` is reusable-only; it inherits the filtered wrappers' invocation
and cannot start independently on a push or PR.
Generated references legitimately change with code inputs; the PR/merge-queue
docs or CLI lane already checked freshness before `required` passed.
The secret scan and the auto-merge conflict watch (below) remain unfiltered
on every main push; Pages retains its existing site-source filters.

Install Lab, GUI distribution/CI/developer setup and macOS aligners/DeepVariant
retain the area-scoped main-push filters from #904 (main-push coalescing),
including the final `!**/*.md` exclusion. Their PR triggers use the same
Markdown exclusion (Install Lab is invoked through PR area routing).
A Markdown-only main push therefore starts only gitleaks and the conflict
watch, plus Pages for site sources; none of these six workflows or the OS
suite starts.
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

### Main-push concurrency and macOS capacity

Main pushes coalesce pending main-push runs independently in Install Lab, GUI
distribution, the four CLI OS workflows, CLI package/benchmark smoke, macOS
Aligners, macOS DeepVariant, GUI CI and GUI developer setup. A started main
run always finishes (`cancel-in-progress: false` for push events); GitHub
replaces an older pending run with the newest pending push. PRs still cancel
superseded commits. Other main-push workflows with concurrency follow the
same non-cancelling policy. The OS and macOS-capacity workflow groups
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
**2** (one CI macOS plus one combined smoke); Markdown-only submits **0** macOS jobs after the
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
Main's red/green issue follows the newest conclusive completed push/nightly
result even when a newer push is queued or running. Pending successors only
suppress retries of the older run; they cannot hide a failure or prevent
reporting recovery. A delayed older result never replaces a newer completed
verdict, and closing an old red-main issue does not certify the pending SHA.

### CLI test time and tiers

Each OS runs one all-tier pytest job with `-n logical --dist loadgroup`: one
worker per vCPU. `-n auto` counts physical cores when psutil is installed, so
it ran two workers on the four-vCPU hosted Ubuntu and Windows runners (#1305,
CI slowdown); macOS's three vCPUs and ARM64's four are physical either way.
Pixi setup, installer validation and reference installation happen once per OS,
not once per tier/shard. The tier definitions remain available for local
diagnosis:

- **Core:** every test not explicitly listed in `INTEGRATION_MODULES` in
  `scripts/ci_test_tiers.py`. New tests automatically join this tier.
- **Integration:** the realignment, analyze-workflow, benchmark,
  benchmark-module, pipeline-interoperability, contig-calling, microarray-reuse,
  native-aligner and DeepVariant modules listed there. Whole modules retain
  their shared fixtures and the existing shared-smoke scheduler.
- Affected generated chrM and whole-genome end-to-end tests run on each selected
  OS (after the all-tier suite). Separately configured imputation,
  ONT and Windows vep-rs checks retain their environments when affected.

Both tiers run together within the affected files on CLI-routed PRs; an empty selection
passes without testing unrelated files. Non-prose main pushes, together with
their merge group, and nightly runs retain full coverage (or exact-SHA
green-main nightly evidence);
merge-queue Ubuntu and code-selected Windows skip only cache hits from main. ARM64 runs its selection in one
job. No slow suite is demoted to nightly-only coverage.
Only the statically proven generic set is Ubuntu-x64-only in CI. Genotyping
modules whose import closure includes command/tool behavior remain cross-platform.
Ordinary local pytest still collects everything; the opt-in `--ci-tier` and
`--ci-skip-independent` and `--ci-shard` flags implement these CI-only partitions.

The former core-1/core-2/integration fan-out traded repeated setup for latency.
Issue #1038 reverses that trade to reduce runner usage and macOS slot pressure.
The first/only matrix entry still seeds shared main caches and runs one-off
checks; ARM64 keeps its existing `native` label. The all-tier x64 label is `all`.
The result-cache identity includes `tier=all shard=all`, so old shard entries
are not reused incorrectly. New main runs seed all-tier `passed.json` entries;
PR and merge-queue reads/records retain their existing trust and selection rules.

Timeouts use recent successful hosted job timings (November 16, 2026), rounded
up from 1.4 times the larger combined main/PR duration. Combined shard time is
a conservative upper bound: the new job removes two setups, not test coverage.

| OS | Main core-1/core-2/integration (min) | PR core-1/core-2/integration (min) | Combined maximum (min) | Timeout (min) |
|---|---|---|---|---|
| Ubuntu | 11.40 / 10.77 / 11.83 | 8.50 / 8.75 / 9.22 | 34.00 | 48 |
| macOS | 14.73 / 12.30 / 14.33 | 12.70 / 11.80 / 15.13 | 41.37 | 59 |
| Windows | 15.13 / 12.63 / 10.17 | 10.10 / 9.50 / 11.63 | 37.93 | 54 |
| ARM64 (already one job) | 14.68 | 12.22 | 14.68 | 21 |

Baseline main run IDs: Ubuntu `43950284692`, macOS `43904396484`, Windows
`43950284545`, ARM64 `43950284595`; PR area run `43937892277`.
Main smoke run `43941593870` used package + benchmark minutes of
0.55 + 1.75 (Ubuntu), 1.13 + 2.58 (macOS), 0.78 + 3.08 (Windows);
the same PR used 0.47 + 1.58, 0.67 + 2.08, 0.90 + 2.65 respectively.
These smoke samples had warm dependency caches; their durations do not bound
a cold Pixi install after `pixi.lock` changes. PR smoke jobs cannot seed the
replacement main cache. All three combined smoke jobs therefore retain the
conservative **30-minute ceiling**, rather than the warm-cache-derived 4/6/6
minutes, until cold lockfile-change runs establish a safe tighter budget.

Per-test timing XML and the slowest 60 durations remain available on **every**
run, separately named by OS. Non-gating line coverage is collected on
main, nightly and manual runs, not PRs; each report covers the all-tier suite.
It uses coverage's `sysmon` core (Python 3.12+ `sys.monitoring`): the default C
tracer added 35–55% to a sampled subset's time locally, sysmon about 5%.

`CLI package and benchmark smoke` runs the existing package-smoke and real-data
smoke benchmark commands sequentially in one job on each of the three x64 OSes.
It has the same CLI-PR routing, non-Markdown main coverage and nightly coverage. Neither
command has been removed. Steps stop at the first failure, preserving the
failure observer's per-job attribution (a later flaky test must not hide a
package regression). The failure observer
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
| Merge-queue group | full Ubuntu CLI suite and, for code changes, full Windows suite (less test files cached as passed on main), plus ancillary required checks selected by the group's diff | macOS/ARM64, package smoke and Install Lab not run |

On PRs, `required` waits for the existing `ready-gate` verdict and requires it
to succeed, so enabling auto-merge cannot bypass a failing selected OS job.
Routing/selection skips and valid self-certifications remain accepted by that
gate. The area gate does not depend on `required`, avoiding a dependency cycle.
Merge groups skip the area gate and retain the same smaller required set;
main pushes run selected workflows in full (Ubuntu and Windows tests minus the
files their merge group ran at that SHA) and nightly runs cover all paths
unless the identical workflow already passed that exact main SHA.
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
draft/label runs do not block a passing ready run. App check runs (linked as
`/runs/ID` rather than `/actions/runs/ID`) count only when the check-runs API
confirms the same repository, the PR head SHA, a completed success, neutral or
skipped conclusion, and an allow-listed app (CodeQL's
`github-advanced-security`); any other non-Actions check fails closed.
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

Auto-merge never enqueues a PR that conflicts with its base, and GitHub
doesn't say so (#1281; one such PR waited about three hours). After every main
push, every three hours and on manual dispatch, *Auto-merge conflict watch*
(`auto_merge_watch.yml`, one five-minute API-only job running
`scripts/ci_auto_merge_watch.py` from main) labels each open PR whose
auto-merge is enabled and whose mergeability is `CONFLICTING`
`auto-merge-blocked`, creating the label if needed, and comments asking the
author to merge the base branch. It comments before labelling, once per PR
head per blockage: a hidden marker in its comment names the head, and only a
marker newer than the label's last removal counts. So a failed run retries
without repeating the comment, while a new head that still conflicts, or a
conflict that returns after clearing, is announced again. One PR's API
failure doesn't stop the others; the run fails at the end. The label is removed
when the conflict is gone, auto-merge is turned off, or the PR closes or merges.
It waits for GitHub to compute an auto-merging PR's mergeability after a push
and leaves it alone if that stays unknown. `--dry-run` reports without changing anything.

Routing (`changes`) also runs `python3 scripts/release_notes.py check --base`,
the stdlib-only release-note fragment check (#1281), so a PR adding only a
fragment under `docs/release-notes/unreleased/` needs no other runner. With the
base (a PR merge commit's first parent, or the queue group's base), it also
fails a release, a change that adds a `vX.Y.Z.md` or bumps the version, while
fragments remain, so none is left out of that release or announced twice.

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
macOS) adds the hg19ToHg38 chain and hs38 reference library. Every caller uses one
`tier: all, shard: all` matrix entry, the `--ci-tier`/`--ci-shard` defaults, so
it collects both tiers. Jobs report as `test-ubuntu / all`
on main and `ubuntu / test-ubuntu / all` in PR area checks (likewise macOS and
Windows); ARM64's single job is
`native-arm64 / native`. Each job uploads `tests-<os>-<label>`. The workflow has
no concurrency group of its own, so the OS calls in one run never cancel each
other.
Smoke contexts change from `smoke (OS, package)` / `smoke (OS, benchmark)` to
`smoke (OS)` (prefixed `smoke / ` in PR area checks). The reusable caller IDs,
`required` and `Ready PR area checks` names and dependencies are unchanged.
Failure observation, red-main matching and flake-ledger retries use the same
normalized PR/main job names; historical shard failures remain historical data.
