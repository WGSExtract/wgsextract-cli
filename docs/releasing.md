# Releasing

One `vX.Y.Z` release ships both the CLI and the WGS Extract desktop app
(`gui/`), which bundles that same CLI release. Most of it is automated; this is
the whole sequence, with what to check at each step.

## 1. The release

Merging the release PR is the whole CLI release: nobody has to push a tag or
run `gh release`, so an agent session that can only open and merge PRs can do
it.

1. **Bump the version** on a branch named `release-X.Y.Z`:

   ```bash
   python scripts/bump_version.py X.Y.Z
   ```

   It sets the version in `pyproject.toml`, `pixi.toml` and
   `src/wgsextract_cli/__init__.py`, and points the app's bundle at the same
   release with `gui/scripts/bump-wgsextract-cli.py vX.Y.Z` (the tag file
   `gui/examples/WGSExtract/scripts/wgsextract-release-tag.txt`, the bundle
   version that names the installers, and an entry in `gui/docs/CHANGELOG.md`).
   The release workflow checks all four versions against the tag. It also
   moves the [CLI site](https://wgsextract.github.io/wgsextract-cli/)'s
   current release (`site_src/index.md` and `install.md`) to the new version,
   and the same script points the [app site](https://wgsextract.github.io/wgsextract-cli/app/)
   (`gui/site_src/index.md`, `docs.md` and `wgsextract.md`) at the new
   release's installers and rebuilds `gui/site/`. The site deploys when the
   release PR merges, so its download links fail until the release's app
   build (below) adds the installers. Those pages say what the release adds only through the
   release notes link, so they need no other edit. The v6 row of
   [wgsextract.github.io](https://wgsextract.github.io/), which sends new
   users to the app site, moves to the release on its own within the hour
   once the release carries the app (the *App release watch* workflow; see
   below).

2. **Write the release notes** in `docs/release-notes/vX.Y.Z.md`, in the same
   PR: what users get, under `## New` and `## Fixes` (see v0.4.2 and v0.4.3),
   plus `## Deprecated` when commands are deprecated, and the app's changes
   (`gui/docs/CHANGELOG.md`'s Unreleased section), ending with a compare
   link (`.../compare/vPREVIOUS...vX.Y.Z`). Start from
   `docs/release-notes/unreleased.md`, where PRs record entries as they land
   (deprecations must be announced there), and rename it; the deprecated
   commands are also listed in `DEPRECATIONS` in
   `src/wgsextract_cli/commands/registry.py`. A command deprecated in one minor
   release is removed in the next, so a release that reaches a deprecation's
   `remove_in` version removes the command. The PRs since the last release are
   `git log --oneline vPREVIOUS..origin/main`.

3. **Open a PR** titled `Release X.Y.Z` and merge it once CI is green.

   On the merge, `release.yml` sees the new version, runs lint, type checks,
   tests and the package smoke test on the merge commit, then creates the
   `vX.Y.Z` tag and publishes the release as the latest with the notes file.
   `install.sh` installs the latest published release. Its last job then calls
   `gui-distribution.yml`, which builds the app at the tagged commit on Linux,
   macOS (Apple Silicon and Intel) and Windows, signs and notarizes the macOS
   app, and adds the installers and the updater feed (`latest.json`) to the
   same release; installed apps update from the latest release's
   `latest.json`. The app's version is the tag's. The native Windows
   tools aren't part of it: `pixi.toml` pins their own packages (see
   [packaging/windows/README.md](../packaging/windows/README.md)).

   Check the *Release* run on the merge commit. The tag isn't created until
   the checks pass. If the run fails, fix the cause and retry from main with
   *Run workflow* on the Release workflow (`gh workflow run release.yml`): it
   picks up where it stopped, publishing from the existing tag if one was
   already created. A release only becomes the latest if no newer version
   already is. If only the app's build failed, rerun the failed jobs, or add
   the app to the existing release with
   `gh workflow run gui-distribution.yml -f tag=vX.Y.Z`.

   The "Protect release tags" ruleset lets only admins, maintainers and
   deploy keys create `v*` tags, so the workflow pushes the tag with a deploy
   key: `RELEASE_TAG_DEPLOY_KEY`, a secret of the `release-tag` environment,
   which only `main` can use. To replace the key, add a new write deploy key
   and set the secret to its private key
   (`gh secret set RELEASE_TAG_DEPLOY_KEY --env release-tag < key`), then
   delete the old deploy key.

Without a notes file the release is left as a **draft** with generated notes.
Edit and publish it on the release's GitHub page (**Edit**, then **Publish
release**), or with
`gh release edit vX.Y.Z --notes-file notes.md --draft=false --latest`.
Pushing a `vX.Y.Z` tag by hand still works too, and also uses the notes file
if the tagged commit has one.

## 2. What the app's release needs

- **Secrets and variables.** The app's build reads the Apple signing and
  notarization secrets (`APPLE_CERTIFICATE_P12`, `APPLE_CERTIFICATE_PASSWORD`,
  `APPLE_SIGNING_IDENTITY`, and `APPLE_API_KEY_P8`, `APPLE_API_KEY_ID`,
  `APPLE_API_ISSUER_ID` or `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`,
  `APPLE_TEAM_ID`), the updater signing key (`TAURI_SIGNING_PRIVATE_KEY`,
  optional `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`) and the
  `TAURI_UPDATER_PUBKEY` variable. A release's macOS build fails without the
  Apple secrets, and without the updater key the release has no `latest.json`.
  See [gui/docs/distribution.md](../gui/docs/distribution.md).
- **Trying a change first.** *Run workflow* on *GUI distribution* (or
  `gh workflow run gui-distribution.yml --ref BRANCH`) builds unsigned
  installers as run artifacts without releasing anything; the Install Lab can
  test them (section 3).
- **The main site's deploy key.** The *App release watch* workflow updates
  the v6 row of `README.md` in
  [WGSExtract/wgsextract.github.io](https://github.com/WGSExtract/wgsextract.github.io)
  with `MAIN_SITE_DEPLOY_KEY`, a secret of the `main-site` environment (limit
  that environment to `main`). Without it the workflow warns and the row
  stays put. To set it up (the first two commands, once) or replace it:

  ```bash
  gh api -X PUT repos/WGSExtract/wgsextract-cli/environments/main-site -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
  gh api -X POST repos/WGSExtract/wgsextract-cli/environments/main-site/deployment-branch-policies -f name=main -f type=branch
  ssh-keygen -t ed25519 -N "" -C "wgsextract-cli main-site" -f main-site-key
  gh repo deploy-key add main-site-key.pub -R WGSExtract/wgsextract.github.io --allow-write -t "wgsextract-cli release watch"
  gh secret set MAIN_SITE_DEPLOY_KEY --env main-site < main-site-key
  rm main-site-key main-site-key.pub
  ```

### Tags and earlier app releases

The app's releases up to v0.6.6 were made in the former
[gui-for-cli](https://github.com/WGSExtract/gui-for-cli/releases) repository
(also tagged `vX.Y.Z`, with the same numbers as the CLI release each bundled),
and stay there. From the first release after v0.6.6, the app has no tags or
releases of its own: it ships in this repository's `vX.Y.Z` release.

Apps up to v0.6.6 look for updates in gui-for-cli's latest release. Until
that repository is archived, copy each release's `latest.json` there so those
installs find the update (once is enough: the updated app checks here):

```bash
gh release download vX.Y.Z -p latest.json -D out/bridge
gh release create vX.Y.Z -R WGSExtract/gui-for-cli --latest \
  --title "WGS Extract vX.Y.Z" \
  --notes "Moved to https://github.com/WGSExtract/wgsextract-cli/releases/tag/vX.Y.Z" \
  out/bridge/latest.json
```

The feed's download links point at this repository's release, so the update
itself comes from here; the old apps accept it because the release is signed
with the same updater key (copy the gui-for-cli secrets, not new ones).

## 3. Proof it reached users

Within the hour of the app joining the release, *App release watch*
(`app_release_watch.yml`) starts a full Install Lab run named
`Install Lab: app vX.Y.Z`: Linux, macOS and Windows, with the CLI from this
tree and with the CLI release the app pins, plus the negative controls. To
start it at once: `gh workflow run app_release_watch.yml`.

The **pinned** jobs are the ones that matter: they set up the app exactly as a
user's machine does, with the CLI release it bundles. The Windows app jobs only
collect evidence; **windows-judge** checks it (and the negative controls), so
the evidence the release works is the pinned jobs and windows-judge passing. See [install_testing.md](install_testing.md)
for what the lab checks, and for trying an app fix before releasing it
(`gh workflow run install_lab.yml -f app_run=<GUI distribution run id>`).

## Optional tool releases

Native tool builds are released separately from the CLI, as prereleases that
never become the latest release:

- **Windows tools** (`windows-tools-N`): Actions > Windows Tools > Run
  workflow. See [windows.md](windows.md).
- **macOS aligners** (`macos-aligners-N`): Actions > macOS Aligners > Run
  workflow. It publishes the Apple Silicon `wgse-bwa` and `wgse-bwa-mem2`
  tarballs built from `native/` (see [native/README.md](../native/README.md)).
  The CLI does not use them yet. Each package carries its own version, printed
  by `--version`: bump `WGSE_BWA_VERSION` in `native/bwa/CMakeLists.txt` or the
  project version in `native/CMakeLists.txt` (`wgse-bwa-mem2`) when it changes.
- **Linux aligners** (`linux-aligners-N`): Actions > Linux Aligners > Run
  workflow. It publishes the `wgse-bwa` (x86-64 and ARM64) and `wgse-bwa-mem3`
  (x86-64) conda packages built from `native/` by `packaging/wgse-bwa` and
  `packaging/wgse-bwa-mem3`, with their CUDA backends on x86-64. Bump
  `WGSE_BWA_VERSION` in `native/bwa/CMakeLists.txt` or
  `WGSE_BWA_MEM3_VERSION` in `native/bwa-mem3/CMakeLists.txt` together with
  the recipe's `version`.
- **macOS DeepVariant** (`macos-deepvariant-N`): Actions > macOS DeepVariant >
  Run workflow. It publishes the Apple Silicon `wgse-deepvariant` tarball built
  from `native/deepvariant/` and its WGS weights converted from Google's image,
  with a `SHA256SUMS` (see
  [native/deepvariant/README.md](../native/deepvariant/README.md)). Bump
  `WGSE_DEEPVARIANT_VERSION` in `native/deepvariant/CMakeLists.txt` when the
  package changes.
- **Linux DeepVariant** (`linux-deepvariant-N`): Actions > Linux DeepVariant >
  Run workflow. It publishes the Linux x86-64 `wgse-deepvariant` conda
  packages (CUDA and CPU-only call_variants) built from `native/deepvariant/`
  by `packaging/wgse-deepvariant`, and the WGS weights, with a `SHA256SUMS`.
  Bump `WGSE_DEEPVARIANT_VERSION` and the recipe's `version` together, then
  point pixi.toml's `deepvariant` feature at the new CUDA package's URL and
  sha256 and run `pixi lock`.
