# CI routing and cache budget

<!-- Throwaway docs-only routing proof; this branch is not for merging. -->

PR area checks diffs the PR's **base...head**, not just the latest commit.
`--no-renames` includes deletions and both sides of moves. Unknown paths run
the full CLI matrix; mixed PRs run the union of their areas.

| PR changes | Checks selected (in addition to routing and the secret scan) | Estimated runner-minutes/push |
|---|---|---|
| Markdown, including `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, native READMEs | Documentation freshness | 1–3 (previously ~125) |
| CLI source/tests | Four-OS CLI matrix, docs, CodeQL | ~90–100 (previously ~125) |
| Installer, packaging, Pixi lock/manifest, CLI entry points | Above plus Install Lab and applicable package checks | ~125; no intended coverage reduction |
| Native source only | Native build/ctest/package workflows and CLI help smoke | Native build cost + <1; removes ~125 of unrelated checks |
| Site sources or `gui/mockups/` only | Pages source checks | 1–2 (previously ~125 for root site changes) |
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
- `scripts/validate_site_sources.py` (existing source/front-matter validation).

There is no existing general Markdown link/anchor checker. Pages source checks
also run both site builders and the app's site-source validator, without
deployment permissions. The secret scan remains on **all main-target PRs**,
including docs: routing must not hide a secret in prose.

The OS workflows and Install Lab run unconditionally on main pushes and nightly,
as do the GUI and native workflows covered by area routing. CodeQL retains
unfiltered main coverage and runs nightly; PRs scan code/configuration paths
only. Releases and manual dispatches remain available. Install Lab can be
forced on any PR with the `run-install-lab` label. Only this label is actionable:
adding it runs Install Lab without repeating the matrix; other labels are ignored.
Label runs cannot cancel a code-validation run, and skipped label-event checks
have distinct names so they cannot supersede real documentation checks.
Its nightly run includes the former weekly negative
controls and pinned-release scenarios.

PR area checks also accepts non-main target branches, so its routing can be
verified with a docs-only draft PR against the implementation branch before
the routing itself lands. Other main-only workflow triggers are unchanged.

## Landing checks

The main ruleset has no required status checks. Workflow/job skips do not block
merging. Do not hard-code a count or require every OS job to exist.
The old ignored `scratch/merge_when_clean.sh` used `>5` / `<=5` count gates;
replace those gates with a nonempty structured report:

```sh
set -o pipefail
gh pr checks "$PR" --watch --interval 90
gh pr checks "$PR" --json bucket,name | python scripts/pr_checks_ready.py
```

The validator requires at least one passing check, permits intentional skips,
and rejects empty reports, pending, failed, cancelled and unknown statuses.
Wait for workflow registration, verify that the head SHA has not changed,
and check reviews/threads and mergeability separately. The autopilot skill
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
| npm | setup-node could save PR-scoped duplicates | One download cache per OS/arch and package-lock hash (~0.1 GB total) |
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

Pixi-binary-only build jobs disable project caching entirely: `run-install:
false` alone would still restore an unused old environment and keep it alive.
Manual mirror/bwa qualification jobs use the same read-only CLI cache rather
than creating additional partial environment sets.
