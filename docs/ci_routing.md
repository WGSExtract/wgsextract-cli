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

The `changes` job also runs `scripts/output_contracts.py check --base <base>`
with the full history it already fetched: about a second, no extra runner.
It compares the merged tree's output schemas with the previous release's
pinned contract baseline as it is on the base, so a PR cannot edit a baseline
to hide a removed stable field ([output contracts](output-contracts.md)).

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
event in the same concurrency group to cancel in-flight ready runs.

Draft-payload events other than `converted_to_draft` use a separate `-draft`
concurrency group. GitHub may deliver a draft `synchronize` event after
`ready_for_review`; its skipped jobs must not cancel the full ready run.
This isolation applies to every PR workflow with cancelling concurrency, not
just area routing. Ready pushes still supersede earlier ready runs, and
`converted_to_draft` deliberately enters the ready group to stop active work.
Draft summaries also use a different check name from `required`: GitHub accepts
skipped checks as passing, so a delayed draft suite must not satisfy the ready
run's merge requirement while its selected checks are still pending.
Workflows triggered directly by `pull_request` key these groups on the PR number.
Their non-PR invocations use a unique run ID and never cancel each other.
No live-state lookup runner is needed: drafts still allocate zero validation
runners, and readiness always retains its own full run.

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

## Weekly test-usefulness review

The Monday workflow reads existing hosted PR, merge-queue and nightly JUnit
artifacts from the previous completed UTC week. It does not rerun or edit
tests. It updates or reopens one bot-owned `efficiency` issue and attaches a JSON file
with pass/fail/skip counts, cumulative and median test time, and source
change dates. Missing or overwritten artifacts, fork runs and tests removed
from the current checkout limit the sample. The report samples at most 40
pre-merge artifacts per OS and stage,
prioritizing failed runs and comparisons at the same SHA, and reports what
it left out. It includes all available scheduled nightly JUnit artifacts.
Times are summed JUnit durations, not saved runner wall time.

- **Move to nightly** needs a test function still present in the checkout,
  no recorded failures, at least 15 pre-merge
  passes across five runs, and at least 120 seconds on OS/suite combinations
  where the same case also passed at night.
- **Possible flakes** failed before passing at the same tested SHA, stage,
  OS and suite. Tool or runner failures can also produce this signal.
  PR comparisons are separate: the same PR head can run against a
  different base, so it does not establish an unchanged checkout.
- **Merge or delete** suggestions use same-scope test bodies with the same
  signatures and marks, at least five shared passing runs and no observed
  failures. Identical bodies are deletion candidates; a one-literal
  difference suggests parametrization. Check intent and fixtures first.

Source dates come from the newest available Linux nightly coverage map from
the last seven days and the main branch's Git history. The map attributes executed
source files to **test modules**, not to individual cases. A missing
attribution is unknown, not evidence that the test covers nothing.

## PR check routing

| PR changes | Checks selected (in addition to routing and the secret scan) | Estimated runner-minutes/push |
|---|---|---|
| Any draft PR | No validation; API-only review request for eligible collaborators | 0 validation minutes |
| Hand-written Markdown, including `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, native READMEs | Only site-source validation if a site source changes; otherwise no docs job | <1 (previously ~125) |
| Generated API/command reference only | Its freshness check in the cheap docs lane | 1–3 |
| CLI source/tests | Affected test files on four OSes (fast tier for broad fallback), input-selected freshness, CodeQL | Exact-head certification can replace hosted tests |
| `.github/**`, `scripts/ci_*.py` | actionlint and CI policy/routing/readiness; one-OS smoke for changed matrix workflow | Small checks, not the full CLI matrix |
| Installer, packaging, Pixi lock/manifest, CLI entry points | Affected OS suites include setup and bundle checks; Install Lab is release-only | Depends on graph reachability |
| Native source only | CLI help smoke and affected test files; release-only native builds are deferred | <1 for CLI help smoke plus affected tests |
| Site Markdown only | Site-source validation, plus command freshness for generated CLI cards | <1–3 |
| Site code/assets or `gui/mockups/` | Pages checks plus pytest for referenced inputs | Usually 1–3; non-light test inputs select the matrix |
| App source | Applicable GUI CI and GUI developer setup; installer/distribution checks are release-only | Depends on the app area |

These are estimates from the pre-routing workflow medians, not billing claims.
Native build/package time depends on architecture and CUDA compilation; routing
does not promise to make those builds faster. The overall 40% weekly reduction
depends on the proportion of docs/site/native PR pushes and must be measured
after deployment.

## Coverage

### Cheap optional-tool integrations (#1199)

`optional_tools.yml` runs exactly one Ubuntu x64 job for selected contamination,
FreeBayes, LongTR, modkit and ExpansionHunter-dependent tests. The PR router intersects the known
affected test files with these suites; an uncertain fallback alone does not
allocate an optional-tool runner. Changes to this workflow, its PR caller/router,
runner, Ubuntu CI or the shared Pixi setup also select the integrations.
Drafts and merge groups do not run this lane. Nightly and release Ubuntu run all
five suites. Manual dispatch of `optional_tools.yml` validates just
this small lane on a branch.
Before the new workflow reaches main, dispatch the existing `ubuntu_ci.yml`
with `optional-tools-only=true` on the branch instead. This explicit manual
override skips its normal jobs; default dispatches, PRs and schedules
retain their existing coverage.

The existing pinned Pixi action installs `dev verifybamid2 freebayes longtr modkit expansionhunter`
under the separate lockfile-keyed `optional-tools` cache family, seeded only from
main. Fixtures are synthetic: no reference bootstrap, full genome or sample
download is needed. Every selected tool must resolve before pytest; every JUnit
report must contain executed tests and zero skips/errors/failures. With complete
certification, the Linux x64 attestation covers the same no-skips command and
optional environments; regular pytest with skipped tools does not count. This
lane never reuses passed-test hashes.

FreeBayes runs its six caller cases, LongTR its tiny repeat suite, and modkit its
two BAM/CRAM parity cases. ExpansionHunter's prefix-gated hg19 catalog regression
uses its existing fake caller boundary; it is not a real caller qualification.
Other tests keep their existing OS coverage.
Measure hosted setup/test duration in the workflow run rather than assuming a
warm-cache cost. Large Clair3/model-backed native DeepVariant integrations and
real-data suites requiring external inputs remain separate follow-ups, not silent
new downloads.

### Setup checks on PRs and the daily backstop

PR installer checks and the Ubuntu app-bundle command test run only when the
complete diff touches their setup inputs: installers, packaging, GUI files,
Pixi/project manifests, CLI parser/registration, workflow/actions or Install
Lab/bootstrap scripts. Parser/registration inputs remain covered because they
can invalidate the app's bundled command lines.
The bundle check also retains its affected `test_cli_parser.py` trigger:
API wrappers and command-registry dependencies outside `cli/` can change the
bundled command contract even when installer setup is unrelated.
Thus a CLI edit unrelated to setup and the parser contract runs **zero** OS-installer steps
and **zero** Ubuntu app-bundle checks rather than four installer steps and one
app-bundle check. Install Lab and the Tauri installer lifecycle suite are
separate release gates; no PR label schedules them.

The four OS workflows run daily on the latest `main`: Ubuntu at 08:23 UTC,
Windows at 08:41, macOS at 08:47 and Linux ARM64 at 08:53. Each also supports
`workflow_dispatch` for on-demand validation. `nightly_guard.yml` now plans
duration shards for Windows, macOS and opt-in balanced Ubuntu dispatches. A
missing plan falls back to the normal full suite. Scheduled runs never skip
coverage because the SHA previously passed. Ubuntu's default serial, uncached
run still refreshes the passed-test cache and selector observation map;
scheduled failures keep the existing red-main issue and miss attribution.
The queue requires its routed smoke matrix, including Windows for CLI runtime
changes even when PR selection discovers no consumers. It uses the merged tree
and does not reuse passed-test hashes or writer attestations.

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
needed by the selection (normally `vep-rs`; also `delly` on macOS; `imputation`,
`pacbio`, `phasing`, and `somalier` for their routed tests), then retain their
wheels too:

```sh
pixi install --locked -e vep-rs
just certify-setup -e default -e dev -e vep-rs --reference
```

Repeated `-e` replaces the default environment list. `--reference` prepares the
pinned bootstrap, liftOver chain and full hs38 FASTA in one host store, then
symlinks `reference` in the checkout to that library. The store defaults to
`out/shared/certification-reference/` **in the primary checkout**. To put it
on another disk, set an absolute path in the primary checkout's
`out/shared/config.toml` before preparing it:

```toml
certification_reference_store = "/data/wgse-certification-reference"
```

Keep the store outside linked worktrees. Reading this optional configuration
requires Python 3.11 or newer; offline cleanup with the default location still
works on Python 3.10. The store keeps separate versions when the pins change.
Setup checks the SHA-256 of the retained release archive, extracted
assets, pinned chain and catalog FASTA once per version. Certification checks
that the verified files and indexes have not changed since then; it neither
downloads nor rehashes unchanged shared data. Treat the linked library as
read-only: use a separate `--ref` directory for experiments that write to it.
If a verified reference or index changed, setup and certification refuse to
reuse it. Stop jobs using that version, move the version directory named in
the error aside, then rerun `just certify-setup --reference` to rebuild it.
The initial setup can download several GiB; run it under the resource
coordinator on shared hosts.

For a new worktree on an already prepared host, `just certify-setup
--link-reference` links the library offline without preparing wheels. Existing
libraries are not overwritten. To reuse the shared library, move a local
`reference/` aside; an explicit `reference_library` must either be the
worktree's `reference` or point to the pinned library. Legacy libraries
with a retained per-checkout bootstrap still work, but must be checked from
scratch on each certification. `just certify-setup --link-hs38 /absolute/path/to/hs38.fa.gz`
is an offline way to replace an individual ignored FASTA in a linked checkout:
it checks the full SHA-256, leaves nonmatching files and sidecars alone, and
should be run only while nothing is writing to that path.

Each checkout still needs its wheel archives. Generic-only and CI-policy-only
certifications do not need reference setup.

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
Uncertified generic tests run once on Ubuntu; nightly retains full coverage.
When `local-ci/generic` and every routed platform status pass on the exact
head, the PR area workflow skips its hosted test suites. Ubuntu tests require
both `local-ci/linux-x64` and `local-ci/generic`; each other OS can still use
its own attestation when certification is partial. Missing, failed or pending
attestations fall back to hosted checks for the missing layers. Linux x64
is also required when optional-tool tests are routed. Non-test gates still run
as configured: routing, lint, type checks, site/contract checks, gitleaks and
CodeQL. Separate GUI and security workflows are not replaced by CLI statuses.
The `required` and ready-gate verdicts still require successful routing and
hosted gates. Merge groups never consume attestations.
Setup refusals, missing inputs and interruptions clear pending attestations with
`error` (“Not certified … hosted CI covers this SHA”), not `failure`. Only a
test runner report containing failed tests produces `failure`. Landing still
requires the passing hosted ready run; non-success local attestations never
make that otherwise-passing run fail the landing gate.

#### Remote certification on owned hosts

Register SSH aliases and their native platforms in a controller-side
`config.toml` (keep the file outside the Git checkout, or ignore it locally):

```toml
[[certification.hosts]]
alias = "seraph"
platform = "windows-x64"

[[certification.hosts]]
alias = "minipc"
platform = "windows-x64"

[[certification.hosts]]
alias = "mahyar-pro-14"
platform = "macos-arm64"
```

Push the branch, then run on the controller:

```sh
just certify --remote windows-x64 --remote-config /path/to/config.toml --platform-only
just certify --remote macos-arm64 --remote-config /path/to/config.toml --platform-only
```

`--remote` accepts a registered alias or a platform. For a platform it tries
hosts in listed order and skips hosts that fail the SSH, checkout, disk or
native-platform probe, or whose controller lock is busy. It does not retry a
failed or interrupted test on another host. `randy-mini` remains the built-in
macOS ARM64 fallback; `just certify --remote randy-mini` still works. If
`--remote-config` is omitted, the controller reads `config.toml` in its
working directory if present.

The controller enforces the same clean, pushed, same-repository open-PR and
writer checks as local certification. It holds a local `agent-coord` lock named
for the selected alias and bound to the controller PID, including for Windows
workers. All controllers sharing a coordinator must use the same alias for a
machine. SSH requires a known host key and noninteractive authentication.
SSH agent, port and environment forwarding are disabled.
Workers need neither `gh` nor a GitHub token; configure the worker checkout
for public HTTPS, without credential-bearing remotes or extra Git hooks.

Prepare an isolated clone at `~/src/wgsextract-cli` on each registered machine
and ensure Git and locked-compatible Pixi are on the SSH user's PATH. POSIX
workers need `python3`; Windows uses OpenSSH and PowerShell with Python from
`pixi install --locked -e dev` in that clone. No administrator access is
needed. Check the worker's resource coordinator before preparing inputs. If
`agent-coord` is present on the worker, the test invocation uses its resource
lease and a worker-side `wgsextract-certification` lock. On hosts without it,
coordinate explicitly with anyone else using the machine.

The bootstrap fetches the exact pushed head and base SHAs over public HTTPS
and creates `~/src/wgsextract-cli/scratch/certify-<head-sha>` as a detached
worktree if absent. Branch names never enter the remote command. If the run
refuses for missing environments, explicitly install the locked environments
in **that worktree**, as in one-time preparation above, then retry. The worker
runs `certify-setup` if retained wheels or reference inputs need preparation,
then executes the same native platform or generic commands as local
certification. Remote input setup may download pinned archives; the test phase
uses `--no-install`.

The worker's filesystem must start with 30 GiB free. A one-second watchdog
stops the worker below 26 GiB to preserve the 25 GiB reserve. Loss of SSH or
30 seconds without a five-second controller heartbeat cancels setup and
tests using a Windows kill-on-close Job Object or the worker's and observed
descendants' POSIX process groups. A fresh SSH connection confirms cleanup
before the controller unlocks the alias, including after a transport failure
or SIGTERM.
If confirmation fails, the controller skips the explicit unlock. The
coordinator releases the lock when the controller exits, even after SIGKILL.
The worker admits only one bootstrap at a time and refuses retries until the
previous run's cleanup is recorded. If the worker supervisor failed before
recording cleanup, confirm the matching run and its descendants have stopped
before removing `active` from `~/src/wgsextract-cli/out/certify/remote-control/`.
The worker checks the native platform, clean tree and HEAD before and after
execution. Logs stream to `out/certify/<sha>/remote-<alias>/<layer>/run.log`;
only the controller publishes `local-ci/<platform>` and (if requested)
`local-ci/generic`, with the alias and full log SHA-256. Missing or mismatched
results produce no successful status. Tests that actually fail produce
`failure`; refusals and interruptions during a run produce `error`.

Windows x64, macOS ARM64 and Linux ARM64 statuses can replace their routed
suite or workflow-only smoke, not a different platform or the package and
real-data benchmark smoke. Edits to remote certification route its supervisor
regressions to native OS smoke jobs even without a product-code change.
Linux x64 certification requires the locked environments for affected optional
integrations, in addition to the normal platform inputs. The fresh Windows
imputation install stays hosted when affected, even with a passing certificate;
certification validates tests against the environment already on its host.
Pure moves still run hosted merged-tree import and collection guards.
Sparse selected PRs save less.

### Decisive test selection rollout

[#1339 (decisive test selection)](https://github.com/WGSExtract/wgsextract-cli/issues/1339)
changes the selection policy in stages.

1. Collect a versioned dependency map on trusted Linux x64 main/nightly runs.
   Record the source SHA, run and attempt, platform, interpreter, lockfile,
   and test/tool profile. Report missing or incomplete evidence explicitly.
2. On ready PRs, report what the decisive selector would choose next to what
   the current selector chooses. Record the diff/base/head and tested tree,
   map provenance, selected and omitted test files, rule reasons, and
   unmatched inputs. The report does not control test or job selection,
   cache reuse, certification, or the `required` result.
3. Enable symbol selection only for regular Linux x64 pytest after stage 2
   lands and the gate passes draft review, local checks and certification.
   The [owner approved enabling it without a 20-PR wait](https://github.com/WGSExtract/wgsextract-cli/issues/1339#issuecomment-7088656005).
   The gate verifies its map on its own PR and retains current selection if
   the map is missing, stale, incompatible or the analysis fails. A
   passing shadow report cannot show that a test absent from both selections
   passed. Keep the existing selector for other platforms.

The decisive rules cover the complete diff, including both sides of moves
and the base tree for deletions:

| Changed input | Select |
|---|---|
| Observed Python function body | Tests that executed the function |
| Module/class initialization, imports, signatures or defaults | Observed module users and literal importers |
| New or unobserved function | Tests of known callers within two static hops, plus explicit symbol and module consumers |
| New Python module | Tests of the head's importers and callers with local imports |
| Shared helpers and fixtures | Helper callers and fixture users; autouse fixtures select their directory scope |
| Data or generated files | Observed and static readers |
| Package/tool manifests or locks | Users of the changed packages or tools in the affected environments |
| Native, packaging, shell or CI inputs | Declared tool/package families, static readers and existing routes |

Always include changed or added tests and applicable contract tests. A
universal dependency such as root pytest configuration, the interpreter or
a root autouse fixture can select the entire lane; name the scope in the
report. An unmatched input is a gap to investigate. Before relying on the
new selector, give it a declared consumer or contract test, or record a
reviewed reason why it has no runtime effect. Unknown inputs do not
implicitly select every test under the decisive policy.

The daily backstop runs the full regular Ubuntu suite on latest `main`
without reusing a prior push's pass. It refreshes the existing test-result
cache ([#915 (passed-test cache)](https://github.com/WGSExtract/wgsextract-cli/issues/915)).
Main pushes can avoid repeating that exact SHA's queue passes, but not the
files the queue skipped. Keep test-selection receipts for the tested trees.
If the backstop fails, rerun only the failing tests on
first-parent main trees since the last comparable full pass to locate the
first reproducibly bad commit. Do not infer a miss from timing or from a
single failure: distinguish omissions from flakes, runner failures and
interactions between merges. File a `ci-automation` issue for a confirmed
omission, naming the PR, failing test and selection rule, and requesting a
selector regression. This does not replace other OS, smoke, installer,
certification, or merged-tree guard coverage, including `python-size`.

The replay estimated 39.2% fewer regular Ubuntu pytest minutes and retained
all 140 independently identified test targets and the known escape tests
and guards. The historical replay is a cost estimate, not evidence from
live shadow runs.

### Current per-test PR selection

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
helpers, shared fixtures, the dependency manifests/lockfile, deleted modules and
unknown paths trigger broad fallback. The server-specific `tests/server_helpers.py`,
`tests/server_data.py` and `tests/server_path_cases.py` are an exception: their
callers and fixture users import them explicitly, so a change selects their
transitive importers and any literal readers. HTTP limits and job-log tests
live in `tests/test_server_http_limits.py`; reference-library and dependency
queries live in `tests/test_server_queries.py`. Both are routed by those
imports in the same way as other server test modules.
On ready PRs a broad fallback means the fast tier
plus known affected tests; diagnostic and local certification selection can
still request the full suite. Eager package
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
`scripts/ci_run_tests.py` to `pixi run test-ci`; the duration plan and the
Ubuntu-only partition apply **within** that set. Empty selections
skip jobs, and empty partitions exit successfully. Selections are
validated and passed as argv, never evaluated as shell text. Explicit paths
are filtered using the `test-ci` task's ignore rules so generated-genome and
real-data suites remain exclusively in their dedicated steps.

Non-Markdown `.github/**` and `scripts/ci_*.py` changes select actionlint and the CI
policy/routing/readiness regressions, not the CLI matrix or Install Lab.
`ci_tests` includes their actual transitive script/action readers as well as
the baseline policy checks; the CI job executes that complete selection on
four pytest workers and reports the slowest tests.
Changing an OS matrix workflow additionally exercises **only that OS** in
one lightweight smoke job; changing shared `os_tests.yml` exercises all four
OS smoke jobs. Mixed product/CI changes union their coverage. Declared docs
freshness modules join a product selection before the docs lane is suppressed.
Extra generated-genome, imputation, ONT and Windows vep-rs checks run only
when their test files are affected. The queue instead runs import and test
collection plus a bounded smoke cross-section on affected CLI areas. Nightly,
manual and release candidate runs retain full-suite coverage.

The selector gives tests the transitive dependencies of their `conftest.py`
files. Keep shared home/config isolation in `tests/home_isolation.py` without
application or CLI subprocess imports. Tests that launch the CLI still reach its
entry point through the literal subprocess target in `tests/smoke_utils.py`.
The selector builds its own policy names from the app package prefix: they name
modules the selector recognizes, not modules the selector executes. Real
dependencies shared by fixtures still count: core configuration can still
reach the CLI through the public API.

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

The lightweight documentation job installs the package and documentation test dependencies
**only when its selected Python tests are nonempty**, never for prose alone.
`DOCS_TARGET_INPUTS` in `scripts/ci_test_dependencies.py`, complemented by exact
test-input reads, selects each target independently:

- `tests/test_site_references.py` (generated command, API and configuration
  reference pages and CLI cards);
- `tests/test_cli_help.py` and `tests/test_help_index.py` (help topics/examples
  and the compact discovery cache);
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
neither; ordinary site prose runs both source validators with system Python
and no package installation. Reference page templates and the configuration
guide also select the site generation test.
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
- the test file's identity, the job (OS, tier, shard, OS-independent skip), Python version, platform
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
| Nightly or manual full dispatch on main | nothing (runs the full suite) | `test-pass-v1-<os>-<label>-main-<run>` |
| Ready PR push or `ready_for_review` | latest `-main-` and this PR's latest `-pr-` manifest | `test-pass-v1-<os>-<label>-pr-<run>` |
| Other refs | nothing (runs uncached) | nothing |

A lookup is exact set membership, so a stale, corrupt, evicted or missing
manifest can only re-run tests, never skip one whose inputs changed. A run
records only when pytest exits successfully (or finds nothing to collect in a
sparse partition); failed or cancelled runs record nothing. A cache entry is
saved only when it learned new hashes. Manifests keep 14 days, 4,000 hashes
and eight trees' names (tens of KB each), so they cannot crowd out Pixi caches.

**Security.** GitHub scopes caches by ref: a PR run writes only to
`refs/pull/<n>/merge`, readable by later runs of that PR alone, and can read the
base branch's (main's) caches. Only `schedule` and `workflow_dispatch`
runs can write main's scope. A PR therefore cannot poison what main or other
PRs read; at most it can skip its own tests, which its own workflow edits could
do anyway. The merge-queue smoke does not read or write these caches.
Nightly reruns the full suite, including tests not selected before merge,
at the latest main SHA.

Each test job's summary lists the cache-skipped files, where they passed
(`main` or `pr`) and their hashes. Each job's
`tests-<os>-<label>` artifact also holds `test-scope.json`: the files selected
for that job and, for each cache skip, where it passed. To estimate savings, run
`pixi run python scripts/ci_cache_replay.py --limit 60 --junit <main pytest.xml...>`;
it replays consecutive ready runs of merged PRs.

There is no existing general Markdown link/anchor checker. Pages source checks
also run both site builders and the app's site-source validator, without
deployment permissions. The secret scan remains on **all ready PRs and queue groups**,
including docs: routing must not hide a secret in prose.

### Main pushes, nightly and release gates

A push to `main` starts gitleaks and the auto-merge conflict watch. CodeQL
retains its Markdown exclusion; Release, Pages and Copilot setup retain
their path filters. No OS suite reruns on push. The full, uncached latest-main
OS suites run nightly. The release workflow also dispatches the four full OS
suites for the exact tag and blocks publication on failure.

Install Lab, Linux/macOS aligners and DeepVariant, macOS Clair3, GUI Tauri
installer, GUI distribution, PharmCAT end-to-end, Windows GKL/GATK, and CLI
package/benchmark smoke run on dispatch only. The release workflow dispatches
them for the exact candidate and blocks publication on failure. There is no
weekly or nightly run of those separate release-only suites. GUI CI and GUI developer setup still
run on matching PRs and their existing schedule, not on pushes. See
[Releasing](releasing.md) for the release checklist.

Markdown-only main pushes therefore start the secret scan and conflict watch,
plus Pages for site sources. See AGENTS.md, "Docs fast path", for the narrower
direct-push permission: policy files and generated references still require PRs.

All standalone `pull_request` workflows exclude prose-only Markdown diffs
through ordered path exclusions or exact non-Markdown includes (Developer
setup and Linux bwa-mem3 parity). Only `pr_ci.yml` starts: its router retains input-selected documentation
freshness, gitleaks and `required`, including generated-reference checks.
Mixed diffs still select the union of applicable code areas. PR event types
and job-level draft gates are managed separately by #910 (draft PR CI gating).

Adding or removing labels does not start or cancel PR area checks. The landing
validator scopes area checks to the selected ready run. PR runs still cancel
superseded commits; nightly and manual runs are independent of new pushes.

Workflow completions and PR events carrying `ci-red-main` share the observer
queue so they cannot update quarantine state concurrently. Other PR events do
no work and use a separate group per run, so a full observer queue cannot
block their startup. The weekly rollup keeps its own group.

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

### CodeQL language scoping on pull requests (#1373)

A ready PR analyzes only the CodeQL languages whose files it changes
(`scripts/ci_codeql_scope.py`): Python for `.py`/`.pyi`, JavaScript/TypeScript
for the extractor's file types (including JSON, YAML, HTML and XML), and
Actions for `action.yml`. Rust is not scanned (owner decision 2026-11-24: about
52 runner-hours a week with no findings). Changes under `.github/` or to the scope script analyze every
language. Main pushes and the nightly schedule always analyze every language.

GitHub's CodeQL check turns `neutral` ("N configurations not found") and
compares no alerts when a PR lacks a category the base branch has, so new
alerts would pass unseen. An unchanged language therefore does not skip its
matrix leg: it re-uploads the base branch's latest analysis for the same
category and job, then confirms (by SARIF ID and commit) that the PR has
that configuration. If the base analysis cannot be found, downloaded or
uploaded, the leg analyzes afresh; an accepted upload that cannot be confirmed
fails the leg, because GitHub rejects a second upload for the same category in
one run. CodeQL is not part of `required`; the landing validator still rejects
a failing CodeQL app check.

### CLI test time and tiers

Each OS runs all tiers with `-n logical --dist loadgroup`: one worker per
vCPU. `-n auto` counts physical cores when psutil is installed, so
it ran two workers on the four-vCPU hosted Ubuntu and Windows runners (#1305,
CI slowdown); macOS's three vCPUs and ARM64's four are physical either way.
Pixi and reference setup run on each allocated runner; installer validation
and dedicated integration checks run once per OS. The tier definitions remain
available for local diagnosis:

Modules that use shared `out/fake_30x` data must import `ensure_fake_data`
directly from `tests.smoke_utils`, even when they use it through a fixture
from `tests/server_data.py`. The scheduler uses that import to keep all
shared-data modules on one worker.

- **Core:** every test not explicitly listed in `INTEGRATION_MODULES` in
  `scripts/ci_test_tiers.py`. New tests automatically join this tier.
- **Integration:** the realignment, analyze-workflow, benchmark,
  benchmark-module, pipeline-interoperability, contig-calling, microarray-reuse,
  native-aligner and DeepVariant modules listed there. Whole modules retain
  their shared fixtures and the existing shared-smoke scheduler.
- Affected generated chrM and whole-genome end-to-end tests run on each selected
  OS (after the all-tier suite). Separately configured imputation,
  ONT and Windows vep-rs checks retain their environments when affected.

**Hosted fast tier:** new or unclassified tests stay eligible. In broad ready
fallbacks, the router excludes only the modules explicitly listed in
`NIGHTLY_ONLY_MODULES`. The initial list uses the November 11–25, 2026
inventory: each module had at least 100 reports, over 1.5 aggregate test-hours
and zero failures. Keep security, authorization, integrity and recent-failure
regressions eligible. A known affected test or a direct edit runs even if the
module is listed as nightly-only. Review the list against fresh inventory
before extending it; remove a module if it starts catching regressions.

**Queue smoke tier:** the router selects the areas and rechecks a small fixed
cross-section on the merged tree, including path authorization, HTTP guards,
output contracts and native Windows text behavior. It imports the CLI and
collects the regular suite, then executes the cross-section without a pass
cache. The smoke job has a 10-minute budget; full suites run nightly and
before release.

Nightly regular pytest runs are uncached. ARM64 runs its selection in one job.
Only the statically proven generic set is Ubuntu-x64-only in CI. Genotyping
modules whose import closure includes command/tool behavior remain cross-platform.
Ordinary local pytest still collects everything. `--ci-tier` and
`--ci-skip-independent` narrow collection; `--ci-shard INDEX/COUNT` is the
local stable-hash fallback.

Ready PRs can fan out regular Ubuntu x64, Windows and macOS pytest without
adding a planning runner: the existing `changes` job reads the last usable
full JUnit reports from successful scheduled runs of that OS workflow on this
repository's `main`. The nightly guard can also size eligible full macOS and
Windows nightlies and main-branch dispatches. To measure a full Ubuntu run
without the daily observation instrumentation, dispatch with
`gh workflow run ubuntu_ci.yml --ref main -f balanced-full-suite=true`.
Merge groups, ordinary main
pushes, ARM64 and the daily Ubuntu observation backstop remain single jobs:
they retain exact-SHA replay/cache contracts or produce a single coverage
context map. Missing or partial timing evidence never drops tests; it falls
back to one job.

The planner sums historical case times by module, assigns the longest groups
first to the least loaded job, and uses a stable hash for unknown groups.
Modules, the shared synthetic-data group and modules declaring xdist groups
stay whole. The target estimated
pytest times are 12 minutes (Ubuntu), 15 (Windows) and 22 (macOS); at most
three jobs on Ubuntu/Windows and two on macOS, where only five hosted slots
are available. It estimates each runner's pytest time by assigning whole
fixture groups to workers, not by dividing a single long test across cores.
It uses fewer jobs if the largest bin would exceed 1.2 times the mean, the
split would not reduce wall time, or aggregate job time including repeated
setup would rise by more than 5%. Small selections stay in one job. Shard summaries report estimated
pytest time before cache skips and actual elapsed time for the test step.
For a sharded Ubuntu PR, a single symbol-selector decision precedes the
split, and one advisory receipt combines the shards' results. The Ubuntu PR
planner reserves 75 seconds for that selector setup and receipt job when
checking both wall time and the 5% aggregate budget. The manual Ubuntu run
does not perform these PR-only steps or include that allowance.

The lead job keeps the `all` label, seeds shared caches, and runs one-off
checks; ARM64 retains `native`. The other jobs use `shard-2` and `shard-3`.
Passed-test manifests keep `test-pass-v1-<os>-<label>` keys, but a module's
hash does not depend on its duration bin. Sharded lanes consume passing hashes
from all main labels when skipping cached work. Queue smoke does not use
the shard planner or store exact-SHA pass receipts.

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
nightly and manual runs, not PRs; each report covers the all-tier suite.
It uses coverage's `sysmon` core (Python 3.12+ `sys.monitoring`): the default C
tracer added 35–55% to a sampled subset's time locally, sysmon about 5%.

`CLI package and benchmark smoke` runs the existing package-smoke and real-data
smoke benchmark commands sequentially in one job on each of the three x64 OSes.
It runs at release preflight and on demand. Steps stop at the first failure
so the run retains the original cause.
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
| Ready pull request | routing, documentation freshness, changed Python lint, CI policy, selected CI Ubuntu checks, secret scan, native CLI smoke, Pages checks, and the area gate covering selected macOS, Windows and ARM64 jobs | separate CodeQL and GUI workflows; release gates do not run |
| Merge-queue group | affected lint/type checks and policy checks, CLI import/collection and bounded Ubuntu/Windows smoke where routed, secret scan and existing lightweight area gates | full OS suites run nightly and on release candidates |

On PRs, `required` waits for the existing `ready-gate` verdict and requires it
to succeed, so enabling auto-merge cannot bypass a failing selected OS job.
Routing/selection skips and valid self-certifications remain accepted by that
gate. The area gate does not depend on `required`, avoiding a dependency cycle.
Merge groups skip the area gate and retain the same smaller required set.
Nightly OS runs cover all paths without reusing a pass on the same SHA.
A job-level `needs:` summary in one workflow, rather than polling the
checks API, reports exactly once per run with no extra permissions or timeout.
The queue's `merge_group` events ignore `paths:` filters, so routing uses the
group's `base_sha...head_sha` diff for ancillary lanes; Ubuntu retains full
test coverage even for docs-only groups.
When a queued PR is pushed to, dequeued or fails, GitHub deletes its
`gh-readonly-queue` ref and those of later groups, then rebuilds them. Each
queue run's `cancel-stale-queue` job (outside `required`, `actions: write` only
there, running the trusted base commit's script) cancels this workflow's still-active queue runs whose ref is gone,
never its own run and nothing when its own ref is missing; API errors are
warnings. The lost-runner observer likewise never retries a cancelled queue run
whose ref is gone.

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
the stdlib-only fragment check for CLI release notes and the desktop changelog,
so a PR adding only a fragment under `docs/release-notes/unreleased/` needs no
other runner. With the base (a PR merge commit's first parent, or the queue
group's base), it also fails a release (a change that adds a `vX.Y.Z.md` or
bumps the version) if fragments remain or the desktop changelog is missing
the release's dated entry.

### Ready admission

The Free plan runs at most 20 hosted jobs at once and GitHub gives queued
jobs no priority, so a burst of ready PRs starves the merge queue (#1373).
Before `gh pr ready`, agents run `python scripts/ci_capacity.py --wait --pr N`.
It reads PRs, workflow runs and jobs through `gh api` (read-only) and exits 0
when admission is fine. The `priority` label, added to PRs from non-bot
collaborators with write access other than `theontho`, lets the named PR skip
the capacity gate. Other PRs exit 3 (hold) while a priority run can still need
a runner, a priority queue entry is not yet `MERGEABLE`, any `merge_group` job
is queued, or more than `--max-ready` (default 4) ready-PR area matrices are
in flight. An empty job list or a running routing job can precede a matrix
fan-out, so neither indicates spare capacity. Once `required` is the only
unfinished job in a ready PR area run and already has a runner, that run no
longer blocks admission. Unknown workflows reserve capacity until they
complete; the coordinator can cancel unstarted agent runs, not reclaim runners
from agent jobs already running. `--wait` polls with
exponential backoff, polling a last time at `--timeout` seconds; `--json`
prints the counts. The check reserves nothing, so each supervisor admits one
PR at a time and starts the next check only once the previous PR's ready run
is visible; concurrent supervisors can overshoot `--max-ready` by at most one
run each.

#### Fast path for collaborators

- **`priority`** is added to PRs from human collaborators with write,
  maintain, or admin access, except the agent account (`theontho`). It is
  added when you open, reopen, mark ready, or convert the PR to draft. You
  can remove it until the next of those events; pushes do not restore it.
  With it, you can ready your PR without waiting for agent capacity, and
  agents reserve runners while your CI can still schedule jobs or your queue
  entry needs checks.
- Add **`hands-off`** if you want to handle everything yourself, including
  CI reruns. Add **`land-asap`** when you're done: owner-account agents may
  rerun cancelled or lost-runner jobs and enqueue your ready PR at the front once
  checks pass. `hands-off` takes precedence. Without `land-asap`, agents may
  only move your existing queue entry after you enable auto-merge or enqueue
  the PR yourself. Agents never push to, ready, or review-fix your PR.

To skip the hosted macOS lane, usually the slowest, run `just certify-setup`
and then `just certify` on your own Mac on the exact pushed PR head. See
[Writer self-certification](#writer-self-certification) for setup and reference
requirements. Batch review fixes into one push to avoid rerunning the full
selected matrix after each fix.

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
| hs38 reference | One main-owned Actions cache (~0.8 GB), keyed to the genome catalog | Not cached; CI downloads the pinned FASTA and prebuilt indexes from GitHub Releases |
| liftOver chain | A new run-id key on each download | One checksum key (<0.001 GB), main-only writes |
| apt | PR cold misses could save duplicates | Main-only writes (~0.1–0.3 GB) |
| PharmCAT reference | Up to two infrequently reused ~1 GB OS-specific entries | Not cached; leave space for the common tool environments |
| Test results (#915) | None | One small passed-hash manifest per OS test job, saved only when it learns new hashes |

Target steady state is roughly **6.6–7 GB**, leaving headroom for CodeQL-managed
caches and lockfile rollover. This is a size plan, not an asserted measured
post-deployment size. Old keys also age out after seven days unused.

The **Actions cache hygiene** workflow runs daily on `main`. It removes a
stale Pixi lock key only after the current lock has a cache for the same
OS, architecture, Pixi version, scope, workspace path and cache archive version.
It preserves equivalent checkout line endings. It keeps two recent
CodeQL overlay bases for each configuration plus any for the exact main tip,
and three passed-test cache generations for each suite on main and on each
PR. Other PR-ref keys and unknown families remain untouched. Preview the plan
in a manually dispatched `cache_hygiene.yml` run on `main`; set `apply` to delete.
The summary records
before and after repository cache bytes and the entries removed. Main moving
during the cleanup stops further deletes.

The shared OS suite downloads `hs38.fa.gz` from `v0.1.0` and its samtools
dictionary, `.fai` and `.gzi` from `ci-hs38-1`. It verifies both SHA-256 pins
in `scripts/ci_hs38.sh` before installing the files beside the bootstrap's
support data, then uses the indexes to read a region before running tests.
Updates to the installer invalidate passed-test hashes. CI does not rebuild
indexes or fall back to upstream mirrors.
To refresh the indexes, dispatch **Mirror reference genomes** with `ci_hs38`
enabled and a new `ci-hs38-N` release tag. The hosted job builds against the
pinned FASTA and publishes just the index bundle. Update the tag and archive
SHA-256 together; retain the old release for older CI revisions. Once this
change lands and older runs have finished, obsolete `hs38-v1-*` Actions
entries can be deleted without removing any other cache family.

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
macOS) adds the hg19ToHg38 chain and hs38 reference library. Each regular
matrix collects both tiers. Its lead job reports as `test-ubuntu / all`
on main and `ubuntu / test-ubuntu / all` in PR area checks (likewise macOS and
Windows); ARM64's single job is
`native-arm64 / native`. Each job uploads `tests-<os>-<label>`. The workflow has
no concurrency group of its own, so the OS calls in one run never cancel each
other.
The release-only package and benchmark workflow keeps its `smoke (OS)` jobs.
The `required` and `Ready PR area checks` names are unchanged.
Failure observation, red-main matching and flake-ledger retries use normalized
PR/main job names, including the shard label.

### Reading the Linux shadow report

On ready PRs that run regular Ubuntu x64 pytest on a hosted runner, the
`test-selection-shadow` artifact records advisory selections: decisive,
tight, symbol, widely-called helpers and line. The job summary shows the current
selection, per-candidate files and timing estimates, and omitted observed
failures. The artifact contains the full file sets, rule reasons, exact tested
merge tree and parents, and the regular pytest receipt. Separate generic,
smoke, and installer jobs are outside this comparison. A certified or
path-skipped Linux job produces no hosted comparison; missing evidence does
not count as a pass.
For eager import changes, the symbol and helper arms also record a separate
non-gating package import probe and keep observed callers of changed bindings.

Scheduled Ubuntu runs execute the full regular suite even when the same
main commit passed earlier that day. They write a versioned Python
coverage-context map and per-file test timings. PRs can restore that read-only
map. An absent map, incompatible lock or unreachable source SHA is reported
explicitly. Test-minute estimates sum recorded pytest durations; they omit
unmeasured files and are not wall-clock predictions. Python tracing does not
measure external tools or every file reader, so an unmatched input is a
measurement gap to investigate, not permission to change CI selection.

The line shadow uses a separate `linux-line-impact-map` artifact and read-only
cache. It reuses the C-tracer contexts from the same successful scheduled or
trusted full manual Ubuntu run; it adds no tracing to PRs. The gzip payload
contains tracked package paths, positive line offsets, and integer IDs into a
test-file table. It contains no source text or parameterized test names.

The line map must match the symbol map's run and attempt, be no more than
48 hours old, and match the tested base's tracked source, test and other
input fingerprints (including fixtures outside `tests/`). The initial
shadow only narrows a modification to one existing function body when every
edited preimage line has attributed callers. Changed test files and independent
routes such as contract tests remain selected. Insertions without a preimage,
mixed input changes, and missing or incompatible evidence retain the enforced
selection with a reason. Tests that cover the same lines are not interchangeable.
The line arm imputes missing per-file durations with the longest measured
file and uses that same estimate for its comparison with the enforced set.

Every candidate includes the newline source scanner for changed package Python,
and the tool-contract scanner for command or CLI Python changes. The historical
oracle's narrower writer-only rule added 19 file-runs and recovered six
complete misses; the broader source rule trades a few more short runs for
coverage of writers that are moved or indirect. Shared-base failures, such as
the tabix failure from the step-resume PR and the unrelated PowerShell
qualification failure from GUI/installer preflight, do not justify adding
edges from those PR changes. Both belong to the nightly backstop. A newly
observed omission in a PR report must be triaged for origin, retries and
incomplete runs before it counts as a miss.

#### Linux symbol gate and daily failure replay

The symbol gate changes the regular Ubuntu PR pytest input, not the jobs,
certificates, other OS lanes, or merged-tree checks. The repository Actions
variable `CI_DISABLE_SYMBOL_SELECTION=true` restores legacy Linux selection
for newly started runs; rerun a job to apply it to an in-flight PR. A missing,
stale (over 48 hours), incompatible or unavailable map retains the existing
input and emits a warning. With a usable ancestor map, the gate adds tests
introduced or changed since recording and observed users of intervening
source changes. For an unobserved Python module, it also follows the static
import closure at the map commit and tested base, so tests of newly added
and deleted modules can still be selected. If an intervening module has no
resolvable test importers, or the drift includes an unobserved package
initializer, non-Python source, unresolved dynamic imports, interpreter
configuration changes or changed test harness inputs, the gate keeps the
legacy selection. A universal scope on a ready PR exercises the routed fast
fallback without cached passes (`universal-fast`); nightly still runs all tests.
The gate's own PR runs the same planner against
its synthetic merge tree even if regular hosted pytest was certified. The
advisory reports remain available and distinguish legacy-selected,
enforced and actually observed files.

Main pushes do not run the OS pytest suite. If the cron run is delayed or missing, run
`gh workflow run ubuntu_ci.yml --ref main` (leave `optional-tools-only` off).
Both triggers run the full regular suite without skipping from either result
cache. A passing run refreshes main's test cache and exports the Linux
observation map as a seven-day workflow artifact, separate from the
evictable Actions cache. Dispatches on other refs cannot publish that map.
The Ubuntu jobs and activation canary use a read-only Actions token to find
the latest compatible artifact from a successful scheduled or full manual
Ubuntu run on main. The artifact digest, run and attempt, source ancestry,
Pixi lock, Python version, and 48-hour freshness limit must all match.
Absent or incompatible artifacts leave the legacy selection in force.
The merge queue does not change its test selection based on this map.
Where the suite provides pytest node IDs, the failure replay reruns only those
cases; a collection error instead replays its one test module. It checks
first-parent trees in order since the last verified passing scheduled or full
manual run on `main`, requires a clean baseline and repeats the first matching
failure signature.
It will not automatically attribute skipped cases, a dependency-lock
change, unobserved trees or an unrelated shared-base failure. To open a
`ci-automation` miss, the first bad commit must map to a PR whose passing run
records an enforced symbol omission on the identical Git tree; the legacy
selection must have included the test. Logs explain unverified outcomes and
the stable case marker deduplicates a confirmed miss across nights.

When nightly replay finds a reproducible failure on the exact Git tree from
an eligible line-shadow receipt, `linux-selector-line-recall` and the nightly
job summary compare how many failing test files the symbol and line sets
included. A file with multiple failing cases counts once. No matched
failures means recall is unavailable, not 100%. This score is advisory and
does not publish issues or affect any gate.
