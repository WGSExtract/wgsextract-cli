# Changelog

All notable changes to this project are tracked here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Fixed
- A run, setup or workspace move whose window disconnected before its progress stream began no longer starts, instead of running (and holding the workspace) with nobody listening; a progress write waiting on a stalled window always settles when the stream is cancelled ([#1234, backpressured progress on disconnect](https://github.com/WGSExtract/wgsextract-cli/issues/1234)).

## [0.8.9] - 2026-11-21

### Added
- A built-in genome viewer: **Genome viewer** in the sidebar, or **View reads** beside a BAM/CRAM/VCF input, shows reads and variants with the matching installed reference, offline. BAM, CRAM and indexed VCF/gVCF results get **Show in viewer**; a single-region **Extract custom** result opens at its extracted locus. It adds local BED, bedGraph and bigWig tracks, installed RefSeq gene spans with gene-symbol search and installed cytobands, and **Connect and view remote file** views files on a WGS Extract server after consent to each transfer ([genome-viewer.md](genome-viewer.md)).
- A **Tools** page, below Reference Genomes, lists every mandatory and optional tool from the dependency check (`wgsextract deps list`): whether it is installed, its version and the Pixi environment it comes from. Optional tools packaged in Pixi environments of their own get **Install** (`wgsextract deps install`) and **Uninstall** (`wgsextract deps uninstall`, after a confirmation naming every tool that environment provides); a tool this computer can't have shows **Not available**, and one the user installs (plink2, Dorado) **Install it yourself**, each with the reason as its tooltip.
- An action that needs a missing optional tool (FastQC with MultiQC merging, `vcf gatk`, `score pgs --plink2-check` and the like) opens the missing-prerequisites dialog as a missing genome does, with **Install and Continue** when the app can install the tool.
- A data source may declare `timeoutSeconds` (15 by default).
- Analysis actions show their result when the run ends, even while the terminal is collapsed. An action with `resultCard: true` gets a result card: the self-contained page `wgsextract --card` writes, with the printed result, warnings, outputs, tool versions and input file. It pops up over the app, naming the `.card.html` it is shown from, and **View Card** shows it again. An action with `openOutput` opens a report its tool wrote in the system's default app. The manifest's `resultDocument.cardArguments` asks commands for the card, and jobs get `?card=1`. WGSExtract shows cards for its info, coverage, identification, haplogroup, Y-STR, typing, VCF QC, trio, full-analysis and manifest-verification actions. It opens the FastQC (MultiQC when made), FastP, MultiQC, Personal Report and PharmCAT reports.

### Changed
- Bug-report actions stay off until setup succeeds; setup checks an already installed Pixi and stops, naming its path and version, when it is too old.
- Bump the bundled WGSExtract CLI installer target to `v0.8.9` ([release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.8.9)).
- Actions take an `advancedTitle`, shown instead of `title` in Advanced mode. WGSExtract's Variant Calling **Default** button is now **BCFTools** in Basic and **BCFTools, Delly** in Advanced, naming the callers each mode can run (Advanced adds SVs and CNVs, called with Delly).

## [0.8.8] - 2026-11-18

### Added
- In Advanced mode each titled section starts collapsed to its header row, with a down arrow on the right to open it and an up arrow to close it again. Sections marked `"collapsible": false` stay open; WGSExtract uses this for its Inputs sections. `"collapsible": "open"` sections start open instead; WGSExtract uses this for Generate Microarray Target Formats.
- Checkbox groups take `"layout": "inline"`: the options side by side, and consecutive inline groups share one row.
- WGSExtract can write a gVCF: the Extract page's Variant Calling section has a **gVCF** checkbox for the Default, GATK and DeepVariant callers.
- WGSExtract's Consensus Genome section describes what it writes and what input works best.
- A data source can name a `transform`, an export of an ES module in the bundle that maps the JSON its command prints to the payload the app shows ([docs/data-sources.md](data-sources.md)). WGSExtract's reference genome dropdowns and Reference Genomes table now read `wgsextract --json ref library --list` through one, and its `list-reference-genomes` scripts, which read the CLI's catalog file from inside its install, are gone. The table and dropdowns show the same genomes, states and sizes as before.
- Data sources take `optionalArguments`, as action commands do: a group is left out when a placeholder in it is empty.

### Changed
- Bump the bundled WGSExtract CLI installer target to `v0.8.8` ([release notes](https://github.com/WGSExtract/wgsextract-cli/releases/tag/v0.8.8)).
- Workspace migration refuses symlink/junction aliases of the same physical folder before removing old state. Legacy My Genomes settings now move their actual library contents, and bundle test runs expand direct users-workspace placeholders against their separate users root.
- Folder-move dialogs now take keyboard focus, contain Tab navigation, cancel with Escape and return focus to the initiating control without restoring a cancelled edit.
- Reference-library setup explicitly selects the configured Reference Data library rather than inheriting the last selected reference FASTA.
- Packaged desktop resources include the production TOML parser. Bound library folder changes show their move confirmation in a dialog available on every page, including Basic mode.
- WGSExtract's Variant Calling section replaces the SNP Call, InDel Call, SV Call and CNV Call buttons with **SNP**, **InDel**, **SV** and **CNV** checkboxes and one **Default** button, which runs `wgsextract vcf call` for the checked types, each into its own VCF. **gVCF** writes SNPs and InDels as one gVCF instead, and **Merged** combines everything into one `variants.vcf.gz` (or `variants.g.vcf.gz`) in place of the separate files; both sit on the checkbox row, in Basic too. The buttons (FreeBayes, GATK and DeepVariant beside Default) follow the checkboxes, and the rest of the section's settings sit below them under **Additional options**, in Advanced.
- WGSExtract's Ancestry Inputs holds only the BAM/CRAM. A new **Haplogroup Analysis** section, shown in Basic too, has the Run Yleaf and Run Mito Haplogrep buttons at its foot, below their options (Advanced only). Y-STR Repeat Counts and its options move to their own Advanced-only section.
- WGSExtract's Run Haplogrep button is now Run Mito Haplogrep.
- WGSExtract's Extract sections are renamed Subset BAM/CRAM, Realign BAM/CRAM, Phase VCF and Create Gene Region BED.
- Settings' Setup section can collapse to its header, in Basic and Advanced; it starts collapsed once setup has run and open before then, while it runs or after a failure.
- WGSExtract's Library Paths sections (My Genomes, Reference Data, Reference Genomes) show in Basic too and stay open, like the Inputs sections.
- WGSExtract calls the dataset library the My Genomes library, and new workspaces keep it in `mygenomes`.
- WGSExtract's FASTQ page moves the MultiQC checkbox and the FastQC and fastp buttons from Inputs into a new **Analysis** section, and Aggregate FastQC reports becomes a section of its own.
- WGSExtract's Microarray page moves its quality and output options (Minimum genotype quality through QC minimum depth) from Inputs to an Advanced-only **Additional Refinements** block after the Generate button. Its sections are renamed Analyze a Microarray RAW Data File (now above the formats) and Generate Microarray Target Formats.
- WGSExtract's Genotyping from BAM/CRAM section says what it holds and that microarray genotyping has its own page.
- WGSExtract's VCF page shows the VCF input in its Inputs section, since every section's commands use it.
- The workspace splits in two. `{{bundleWorkspace}}` (the app's copy of the bundle, its saved state and run logs) goes back to the app's data folder, where the user never moves it. The new `{{usersWorkspace}}` (manifest `defaultUsersWorkspace`, WGSExtract's `~/wgs-extract`) holds the settings file and the folders the user manages: the My Genomes library and, with the app-installed CLI, the Reference Data and Reference Genomes libraries and the temporary folder. The Settings page's **Bundle Workspace Location** is now **Users Workspace Location**, and moves the users workspace; changing one of those folders under **Users Workspace Settings** (formerly **Edit Settings File**) asks whether to move what is in it too (setting `moveContents`). The workspace kept in `~/wgs-extract` by 0.8.5 to 0.8.7 becomes the users workspace on the first start, keeping its files; the bundle's copy and state move to the app's data folder. Uninstalling removes the bundle workspace and asks before deleting the users workspace. Scripts get `WGSEXTRACT_GUI_USERS_WORKSPACE`, and `bundle-test-cli` takes `--users-workspace`. Saving settings now keeps keys the editor doesn't show, which the CLI may have saved.
- WGSExtract leaves the Output Directory unset by default: commands use the CLI's default `wgse` folder beside their input files from the lab. Only Advanced mode can set a GUI override. A config setting can be `advancedOnly`: hidden and omitted from GUI command overrides in Basic, without erasing its saved value or changing CLI defaults. Saving visible settings preserves omitted advanced settings.
- Setting-folder moves refuse sources containing either workspace, the bundle source or any settings editor's config file, including through symlinks. Moving the whole users workspace remains available through Users Workspace Location.
- WGSExtract's Dataset Library is now the My Genomes library: the `mygenomes_library` setting (the CLI still reads `dataset_library`) and a `mygenomes` folder. An existing `datasets` folder stays where it is, preserving saved input paths.
- The Settings page shows Setup, **Standard UI Options** (formerly Standard Options), then Dependency Checks, Report a Problem, Users Workspace Location and Users Workspace Settings, the last four closed until opened. A page section can say `"collapsed": true` to start closed in Basic too; it uses the shared section toggle.
- The rest of the GUI for CLI name goes: the app and its docs call themselves WGS Extract GUI, and internal names (window events, temporary folders, browser storage keys, the npm and Python package names) use `wgsextract-gui`. The workspace marker reads `WGS Extract GUI workspace`; workspaces marked by earlier releases are still recognised and get the new marker on the next start. The window layout (sidebar and terminal sizes) is remembered under new keys, so it resets once. Developer tools keep Node in `~/.local/share/wgsextract-gui-dev` and their caches in `wgsextract-gui-dev`, so `make setup` downloads Node again once.
- The command output pane is now the Jobs pane. Its first tab, **Jobs** (formerly Main), lists every job run this session, newest first: its tab, outcome (running, succeeded, finished with warnings, failed or cancelled), start time and main input file. Clicking a job opens its tab at the right end of the row, reopening it if it was closed. As job tabs pile up, the newest stay next to the copy and hide buttons and the oldest drop off the left, so those buttons are never pushed out of view. While the Jobs list itself shows, the row holds only the Jobs tab, since the list's rows repeat the job tabs. Hiding the pane keeps its row of job tabs, and the arrow at its end turns up to show it again; the floating terminal button is gone. Basic mode starts with the pane collapsed; Advanced reopens it as it was last left.
- An action can name its main input with `primaryInput` (a placeholder), shown in the Jobs list's File column; without it the list shows the first file path control the command uses. WGSExtract sets it where that would be an output or a folder (MultiQC, Generate Batch, reference downloads).
- WGSExtract's Datasets page is now **My Genomes**.
- WGSExtract's Reference Genomes delete button runs `wgsextract ref library --delete`, and its `delete-reference-genome` scripts are gone. It asks first and refreshes the table as before; it now also removes the bwa-mem2 index, dense suffix-array files and run manifests, and refuses while another run is building the genome's bwa index.
- WGSExtract's My Genomes test genome buttons run `wgsextract ref test-genome --install` and `--delete`, and the `test-genome-library` scripts are gone. Installing still downloads the SHA-256-pinned zip into the dataset library's `.downloads` folder (now resuming an interrupted download) and unpacks it with its `genome-config.toml`; deleting still removes both, and now unlinks a linked dataset folder instead of refusing.
- WGSExtract's last wrapper scripts are gone: Unalign to FASTQ runs `wgsextract bam unalign` (which now names the FASTQs `<input>_R1.fastq.gz` and `_R2` itself), FastQC runs `wgsextract qc fastqc --r2 … --multiqc`, the BAM page's Detailed Coverage button reads `wgsextract --json info calculate-coverage --status` through `transforms/coverage-status.mjs`, and the library, microarray, VCF and My Genomes sections read `wgsextract ref status --values` directly. FastQC runs now also save a result document.

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
