# Changelog

All notable changes to this project are tracked here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Changed
- The rest of the GUI for CLI name goes: the app and its docs call themselves WGS Extract GUI, and internal names (window events, temporary folders, browser storage keys, the npm and Python package names) use `wgsextract-gui`. The workspace marker reads `WGS Extract GUI workspace`; workspaces marked by earlier releases are still recognised and get the new marker on the next start. The window layout (sidebar and terminal sizes) is remembered under new keys, so it resets once. Developer tools keep Node in `~/.local/share/wgsextract-gui-dev` and their caches in `wgsextract-gui-dev`, so `make setup` downloads Node again once.
- The command output pane is now the Jobs pane. Its first tab, **Jobs** (formerly Main), lists every job run this session, newest first: its tab, outcome (running, succeeded, finished with warnings, failed or cancelled), start time and main input file. Clicking a job opens its tab, reopening it if it was closed. Hiding the pane keeps its row of job tabs, and the arrow at its end turns up to show it again; the floating terminal button is gone. Basic mode starts with the pane collapsed; Advanced reopens it as it was last left.
- WGSExtract's Datasets page is now **My Genomes**.

## [0.8.7] - 2026-11-11

### Added
- Bundles can declare a `prerequisites` check that runs before an action. When the action needs data that isn't installed, it doesn't start; a dialog names what is missing and offers **Install and Continue**, **Continue** (after installing it yourself; the app checks again) or **Cancel** ([docs/prerequisites.md](prerequisites.md)). WGSExtract checks with `wgsextract ref needs`, so running Info, variant calling, extraction and the rest on a CRAM whose reference genome (for example hs37d5) isn't installed offers to install it first, as the old WGS Extract did. The VCF annotation commands do the same for their annotation data.
- Options can give `groupTooltip` and `categoryTooltip`, shown beside their group's heading or row title and their category's heading. WGSExtract's Microarray formats use them for Consumer Tests, Reich Lab and AADR; the AADR file-format note moves from the section description to Reich Lab.
- A settings file (`configFile`) can list `previousPaths`. On load the app moves the file from the first that exists to its declared path, removes the old folder if that leaves it empty, and treats a saved choice of an old path as the declared one.

### Changed
- Bump the bundled WGSExtract CLI installer target to `v0.8.7` ([release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.8.7)).
- The app keeps its own data in `WGSExtract-gui` in the platform's app data folder (`%LOCALAPPDATA%\WGSExtract-gui`, `~/Library/Application Support/WGSExtract-gui`, `~/.local/share/WGSExtract-gui`), and setup installs the WGS Extract CLI there (`runtime/wgsextract-cli`) instead of in the workspace, so the workspace holds only the user's files and moving it no longer reinstalls the CLI. Bundles reach the folder as `{{appData}}` and their scripts as `WGSEXTRACT_GUI_APP_DATA`. The desktop app's identifier is `dev.wgsextract-gui` (its webview data and logs). Environment variables are renamed from `GUI_FOR_CLI_*` to `WGSEXTRACT_GUI_*` (`GUI_FOR_CLI_APP_SUPPORT_NAME` is now `WGSEXTRACT_GUI_APP_DATA_NAME`), and the uninstall CLI's `--app-support-name` is now `--app-data-name`, optional. Installs up to 0.8.6 migrate on the first start: the app's settings come over from `dev.guiforcli.web.embed.wgsextract`, setup is offered again since its CLI is gone, and once it has installed the CLI in the new place it removes the old one from the workspace (on Windows, with its Pixi environment). The Windows installer removes a 0.8.6 app installed in another folder than the new one.
- WGSExtract's `config.toml` moves from `<workspace>/settings/` to the workspace folder itself (`~/wgs-extract/config.toml` by default), and the `settings` folder goes. Existing installs' files move on the next start. A relative path in it, such as a custom Y tree's `tree = "mytree.json"`, now means a file in the workspace folder.

### Fixed
- Linux AppImage: setup, actions and uninstall no longer run with the AppImage's library and tool paths (`LD_LIBRARY_PATH`, `PATH`, `PYTHONHOME` and the like). The system's programs loaded the image's older libraries instead of their own, so setup failed on newer systems such as Ubuntu 26.04 (`libcrypto.so.3: version 'OPENSSL_3.3.0' not found`).
- Windows: saving settings no longer fails with `EPERM` when another program has the file open for a moment, such as the CLI reading its `config.toml` or a virus scanner. The save retries for up to about two seconds.

## [0.8.6] - 2026-11-09

### Added
- A Basic / Advanced mode switch in the sidebar, saved with the app's state. Pages, sections, controls, options, actions, columns and library rows can be marked `advancedOnly`; Basic runs commands with hidden settings at their defaults. Sections can be `subsection`s, dropdowns take `emptyText`, and checkbox groups can lay options out in vendor rows (`layout: "rows"`).
- WGSExtract: Advanced mode covers the CLI's remaining user options, with a new Workflows page. Detailed Coverage shows the depth bins; Extract has MT-only, Y-only, Y and MT, and Autosomes and X buttons; Library Install buttons turn into Delete.

### Changed
- WGSExtract's Library page splits into Reference Data and Reference Genomes, and a new Datasets page (just above Reference Data) holds User Datasets: the Dataset Library folder (`dataset_library`, formerly `genome_library`; new installs use `<workspace>/datasets`), the Test Dataset and Example Datasets. A new Reference Genomes Library setting (`reference_genome_library`, new installs `<workspace>/genomes`) holds only the reference genomes; existing installs keep their folders. The Manage Reference Genomes table shows sizes for FTP-hosted genomes too. Downloading Example Datasets over 25 GB (every example, about 441 GB, or a large tag) asks first. Manage Reference Data's buttons show in Basic; Annotate moves above Workflows.
- WGSExtract pages reorganised around Inputs sections; Align, Realign and Variant Calling move to Extract and VCF annotation to the VCF page.
- Languages trimmed to English, French, German and Brazilian Portuguese (`pt-BR`); other Portuguese locales fall back to it.
- Bump the bundled WGSExtract CLI installer target to `v0.8.6` ([release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.8.6)).

## [0.8.5] - 2026-11-07

### Added
- Bundles can declare a local `jobServer`, and actions can carry a `serverOperation`. Those actions then run as jobs on the server, with live progress and clean cancellation, and run their command when the server isn't available ([docs/job-server.md](job-server.md)). The WGSExtract bundle runs its coverage actions on `wgsextract serve` once its bundled CLI supports `serve --ready-json`; until then they run their commands.
- Bundles can declare which commands print a JSON result document (`resultDocument`). The app then lists the files a run wrote with **Open** and **Show in Folder**, shows its warnings and key metrics, shows a refused option next to the action, and offers **Open Setup** when a tool is missing ([docs/result-documents.md](result-documents.md)). Bundle test plans can check the document with `expectedResult`. The WGSExtract bundle reads its `wgsextract` actions' documents through `--result-file`, and its wrapper scripts exit 2 when they refuse an input ([#529](https://github.com/WGSExtract/wgsextract-cli/issues/529)).
- WGSExtract controls for CLI `v0.6.6`: DeepVariant model selection including `ONT_R104`, SV long-read modes and tandem-repeat BED, Clair3 and methylation pileup (hidden on Windows), Y-STR panels, variant tables, and a redacted bug-report preview. New options start unset, so automatic Nanopore detection and `config.toml` defaults keep applying.
- WGSExtract's Settings page has **Open Bug Report on GitHub** next to **Preview Bug Report**. After a confirmation, it runs `report-issue --open` on the same run, with your description, and your browser opens GitHub's pre-filled new-issue form. The redacted report reaches GitHub in the link, but no issue is created until you submit it there ([#528](https://github.com/WGSExtract/wgsextract-cli/issues/528)).
- Page sections accept `visibleWhen`, and conditions can read the built-in `hostPlatform` placeholder (`windows`, `macos`, `linux`, or `posix`).
- Commands and conditions can name the last action run in the app, and the last failed one, with `{{lastResultDocumentPath}}` and `{{lastFailedResultDocumentPath}}` ([docs/result-documents.md](result-documents.md#the-last-run)). WGSExtract's **Preview Bug Report** uses them, so it describes the action you ran rather than a background library check.

### Changed
- Bump the bundled WGSExtract CLI installer target to `v0.8.5` ([release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.8.5)).
- The WGS Extract workspace now defaults to a visible `wgs-extract` folder in your home folder, not the app's data folder, and holds temporary files and run logs too. The Settings page can move it, with its contents, to another disk; setup then reinstalls the CLI runtime there. Paths inside it are shown as `{{bundleWorkspace}}/...`. Existing workspaces are not moved over: run setup again. Run logs keep the newest 100 runs. If a folder or file of yours is already called `wgs-extract`, the workspace goes in `wgs-extract-2` instead.
- Uninstalling asks before deleting the workspace, wherever it is. That includes one left in the app's data folder, which deleting the app's data now keeps; a silent uninstall keeps them all.
- Bundle WGSExtract CLI `v0.6.6`: Oxford Nanopore reads are detected automatically, keep their methylation tags through alignment, and get Nanopore-tuned calling (DeepVariant ONT model, Sniffles structural variants, optional Clair3), plus CpG methylation pileups. Also adds native Linux ARM64, Y-STR genealogy panels, queryable variant tables (TSV/SQLite/Parquet), depth-aware confidence labels, `report-issue` for redacted bug reports, and easier help; DeepVariant now accepts CRAM 3.1 input and bounds its whole-genome memory use, and whole-genome realignment streams without FASTQ intermediates ([CLI release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.6.6)).
- Bundle WGSExtract CLI `v0.6.5`: fresh Windows installs work again and invalid configuration values produce clear warnings. Includes the v0.6.4 opt-in MultiQC, expanded microarray/AADR exports and targeted Y analysis, streamed eligible small paired realignment, and safer downloads, outputs and Windows cancellation ([v0.6.5 release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.6.5), [v0.6.4 features](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.6.4)).
- Bundle WGSExtract CLI `v0.6.3`: alignment output no longer depends on thread count, BWA-MEM2 sorts in less memory, alignment checks memory before starting and falls back to BWA when MEM2 won't fit, long jobs show memory and disk estimates up front, and SNP/indel calling can optionally run per chromosome in parallel. Native Windows is now validated on full genomes ([CLI release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.6.3)).

### Fixed
- WGSExtract **Run Yleaf** works again: it no longer sends `--yleaf-path`, which the CLI dropped when Yleaf became built in, and the dead Yleaf path settings are gone. Run Yleaf gains Y tree, ancient DNA and genotyped-positions-only options. CI now parses every bundle command with this repository's CLI, so a dropped or renamed flag fails CI ([#538](https://github.com/WGSExtract/wgsextract-cli/issues/538)).
- The Web UI server rejects an invalid `--port` (not a decimal integer from 0 to 65535) with a clear error before it starts, instead of failing later inside Node.

## [0.6.2] - 2026-10-27

### Changed
- Bundle WGSExtract CLI `v0.6.2`: Windows setup now runs natively without WSL or MSYS2, with faster Windows tools. This release also adds microarray templates and Ultima support, and fixes microarray output ([CLI release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.6.2)).
- GUI for CLI is now one TypeScript app packaged with Tauri for Windows, macOS, and Linux. macOS releases ship the Tauri app (signed, notarized, and self-updating through `latest.json`) instead of the SwiftUI app and Sparkle; the macOS app is no longer named `WebUI`.
- Every bundle load validates the manifest and pages against the `docs/schema` rules (required fields, unique ids, paths that stay inside the bundle, text icons, action commands), as the Swift CLI's `bundle validate` did. `npm --prefix app run bundle:validate -- <bundle>...` checks bundles without the app, and `make lint` runs it over the examples.
- Bundles load from `.zip`, `.tar`, `.tar.gz`/`.tgz`, and gzipped `manifest.json` archives on every OS, extracted in pure Node. **Load Bundle...** (Ctrl/Cmd+O) now opens a file dialog for a bundle's `manifest.json` or a bundle archive.
- macOS releases ship separate Apple Silicon and Intel builds. Their DMGs are notarized and stapled, so Gatekeeper accepts the downloaded DMG as well as the app inside it.
- The macOS app has **File > Uninstall** again, as the SwiftUI app did: it runs the bundle's uninstall steps, removes the app's data and WebKit storage, and moves the app to the Trash. `GUI_FOR_CLI_AUTO_RUN_UNINSTALL=1` runs it unattended on macOS as on Linux.
- `make` targets (and `powershell -ExecutionPolicy Bypass -File .\make.ps1 <target>` on Windows) run through a small task runner, `tools/tasks.py`: `run`, `dev`, `tauri`, `build`, `build-tauri`, `lint`, `test`, `ci`, `package`.
- Bump the bundled WGSExtract CLI installer target to `v0.6.0`: male genomes get chrY calls and haploid chrX (they were called as female), `vcf trio` from VCFs finds de novos again, AncestryDNA kits fill in chrX/chrY/MT, DeepVariant runs on CPU from Google's Docker image, alignment uses bwa-mem2 when the reference has its index, and about 60 other fixes from three test passes.
- Bump the bundled WGSExtract CLI installer target to `v0.5.0`: alignment is ALT-aware on hs38DH and always marks duplicates, `info` shows whether an alignment was made that way (WGS Extract v4's weren't) and callers warn about it, microarray kits agree with real chips more closely (99.1% to 99.8% on AncestryDNA), `vcf trio` genotypes a trio jointly from their BAM/CRAMs, and `--gpu` aligns and calls on an NVIDIA GPU with Parabricks.
- Bump the bundled WGSExtract CLI installer target to `v0.4.3`: actions that pick a reference themselves use the one shipped beside a BAM over a library genome of another build (with hs38 installed, the mtDNA haplogroup action reported no haplogroup for the hg19 test genome), SpliceAI scores from Ensembl, and 23andMe v1/v2 microarray templates.
- Bump the bundled WGSExtract CLI installer target to `v0.4.2`: large reference downloads over 8 connections that resume, `ref status` listing all installed data, a usable PharmGKB annotation, `vcf pharmcat`, and a faster `vep --preset fast`.
- Bump the bundled WGSExtract CLI installer target to `v0.4.1`, whose Windows installer no longer needs Git (setup on a PC without Git failed installing Yleaf), runs GATK and HaploGrep on Windows, and removes MSYS2 on uninstall.
- Bump the bundled WGSExtract CLI installer target to `v0.4.0`, which adds the `--config` option the bundle now uses to keep its settings in the workspace, and fixes haplogroups for installs under paths with spaces.

### Removed
- The SwiftUI macOS app, Swift CLI, iOS and AppKit apps, Electron, NodeGUI, the TypeScript TUI, the macOS WebView shell, the Rust/Go/C/C++/.NET/Dart/Kotlin/Python/Mojo experimental renderers, and the benchmark, screenshot, and macOS updater-e2e tooling. They are preserved on the `old-experimental` branch.

## [0.3.8] - 2026-06-09

### Changed
- Bump the bundled WGSExtract CLI installer target to `v0.3.8` so new GUI builds pick up the microarray output fixes.

## [0.1.14] - 2026-06-08

### Added
- Local CI runner (`tools/ci/ci_local.py`) that mirrors the GitHub Actions pipeline; expose as `make ci` (full) and `make ci-fast` (skip iOS).
- Linux Swift Package CI job (Ubuntu, Swift 6.2 container) that runs `swift build` + `swift test` against `GUIForCLICore`.
- App-layer iOS guard: action buttons are disabled with an explanatory tooltip on iOS, where command execution is unsupported.
- Smarter pre-push hook: branches matching `release/*` run the full CI pipeline (incl. iOS build); other branches still use `--fast`.
- `LocaleMatchingTests` for `BundleSourceLoader.matchLocalizationCode` covering exact, region-stripped, Chinese script, and ordering paths.
- Windows admin setup mode for packaged WGSExtract installers.
- macOS and Windows worktree setup targets for installer lifecycle validation.
- SmartScreen install guidance on the WGSExtract download page.

### Changed
- Refactor blitz: split the four largest files into focused topic groups via Swift extensions.
  - `BundleSourceLoader` → entry + `+Manifest` + `+Localization`
  - `TerminalLogStore` → state/public API + `+Setup` + `+ProcessRunner`
  - `BundleManifestValidator` → entry + `+Pages` + `+Controls` + `+Helpers`; the monolithic `validate(_:)` body is now per-page/section/control helpers
  - `ControlRenderer` → view dispatch + `+Subviews` + `+DataSource`
- Round 2 splits: `DataSourceRunner` → entry + `+Process` (macOS); `ConfigFileBootstrapper` → entry + `+Toml` + `+Script`; `BundleSessionLoader` → entry + `+Workspace` + `+InitialState`.
- Moved reusable renderer/session helpers (`CommandRenderContext`, rendered commands, data sources, config IO, and bundle session loading) from `platform/apple/shared/app` into `GUIForCLICore`.
- CI workflow now runs the same Python script CI uses, so local `make ci` and remote CI cannot drift.
- Bumped `actions/checkout` to `@v6` (Node 24 runtime).

### Fixed
- `LocaleLinterRunner` strict mode regex no longer matches `, 0 errors`/`, 0 warnings`.
- `DataSourceRunner.signature` array literal split so Swift 6.2 type-checker doesn't time out on iOS Simulator builds.
- Cloudflare-blocked `install.tuist.io` script in CI replaced with `brew install tuist`.
- macOS CI runner now explicitly selects the newest installed Xcode so Swift 6.2 toolchain is active.
- WGSExtract macOS setup preflight behavior before bundled CLI install.
- WGSExtract installer lifecycle coverage for setup timing, app data creation, uninstall hooks, and cleanup.
