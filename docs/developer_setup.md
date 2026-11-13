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

`deps check` marks tools in optional Pixi environments (GATK, VEP, DeepVariant, ...) as missing. That's expected: install one when you need it with the `pixi install` command `deps check` prints. When the script installs Pixi or `just`, open a new terminal before using them (or add `~/.pixi/bin`, `%USERPROFILE%\.pixi\bin` on Windows, to `PATH`). Rerun the script at any time; `just setup` does the same once `just` is installed. `--skip-tests` (`-SkipTests` on Windows) skips the smoke tests.

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
- Give every runnable command an example and an outputs line in
  `src/wgsextract_cli/commands/_help_catalog.py`.
- Register its parser in `src/wgsextract_cli/cli/registration/`, keeping
  execution imports inside handlers. `cli/_lazy.py` defers loading command
  implementations until execution; help must not import scientific libraries.
- Run `pixi run gen-command-docs` to regenerate `docs/commands.md` and the
  command cards on the site's CLI page (`site_src/cli.md`), plus the bundled
  `assets/cli/help-index.json` used for fast, configuration-independent discovery.

`tests/test_cli_help.py` fails until all three are done.

### Generated references and parallel PRs

Run `just regen` (or `pixi run python scripts/regen.py`) after changing CLI/API
help. It updates the API snapshot, `docs/api.md`, `docs/commands.md`, the bundled
compact discovery index, and the generated command cards in `site_src/cli.md`.
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

The "Start from what you have" paths in both come from `JOURNEYS` in
`registry.py`. To retire a command, don't delete it: add it to `DEPRECATIONS`
there with its replacement and the next minor version as `remove_in`, and add
it under `## Deprecated` in `docs/release-notes/unreleased.md`. A deprecated
command keeps working, warns `deprecated_command` when run, and is listed apart
in the help and docs.

## Write tests

A good test fails when the behaviour a user relies on breaks, and survives a refactor that keeps that behaviour.

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
