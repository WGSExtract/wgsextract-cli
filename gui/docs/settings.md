# Settings

The **Settings** page (at the bottom of the sidebar) controls initial CLI setup,
appearance, language and network mode, bug reporting, and your workspace
location and `config.toml` preferences.

The app's version shows under its name at the top of the sidebar. It turns red
when it is out of date: when a newer release is available, and always in a
development run (a server started from a source checkout), which shows the
latest release's version followed by **Dev**.

## Setup

The **Setup** card at the top of Settings installs and verifies the bundled
WGS Extract CLI and its core bioinformatics tools:

- **Run Setup / Rerun Setup:** Installs the locked WGS Extract CLI runtime into
  the app's data folder and verifies its core dependencies. You can cancel a
  running setup at any time. Setup needs a **Network mode** other than **Off**.
- **Check for app updates:** The first step. It shows the result of the
  desktop app's update check at startup, and rerunning setup checks again, so
  one click also tells you whether the app itself has an update. It shows
  **Off** when **Allow check for updates** is unchecked or **Network mode** is
  **Off**. The browser development server has no updater and shows
  **Unavailable**.
- **Step list:** Expands automatically before first setup, while setup is
  running, or if a step fails; collapses cleanly once setup succeeds (click the
  chevron to inspect steps anytime).
- **Open Bundle Workspace:** Opens the app's internal bundle workspace folder
  (run logs and saved UI state) in Finder / File Explorer.
- **Network mode** (at the bottom of the card): **Automatic** (the default)
  uses Low on metered, constrained or unknown-cost connections, and Full
  otherwise. **Full** has no restrictions; **Low** asks before large downloads.
  **Off** is the one switch for all network activity, including the update
  checks at startup and during setup; a red **NETWORK OFF** badge shows while
  it is on.
- **Allow check for updates** (below Network mode): On by default; choosing
  **Not now** at first launch turns it off. It lets the app check GitHub for a
  newer version at launch and when setup runs, so you can use **Low** without
  update checks. **File → Check for Updates...** still works while it is off.
  It is greyed out and unchecked while Network mode is **Off**.

WGS Extract cannot run its commands until setup has completed once.

> **Windows note:** Setup installs Pixi tool environments under
> `%LOCALAPPDATA%\WGSExtractPixi` for the current Windows user account. If you
> switch Windows user accounts or delete that folder, rerun **Setup**.

## Standard UI Options (Appearance and Language)

Available in both Basic and Advanced mode, collapsed by default (click the
chevron to open it):

- **Language:** Defaults to **Use system default** (matching your OS language).
  You can switch explicitly among available languages; AI-translated locales are
  labeled clearly in the dropdown.
- **Icon Set:** Choose between **Bootstrap Icons** (crisp vector icons) and
  **Emoji** icons.
- **Color Theme:** Follow your OS (**System**), or force **Light** or **Dark**
  mode.
- **Web Font:** Choose the default system font priority or prefer **SF Pro**
  when available.

See [Privacy and network access](privacy.md).

## Report an Issue

Collapsed by default in both Basic and Advanced mode. Expand it when something
fails and you want to report a bug:

- **Last failed run:** Check this to report the most recent failed action rather
  than the most recent run overall.
- **Description:** Optional summary of what you expected to happen.
- **Preview report (`--print`):** Runs `wgsextract report-issue --print` locally
  and prints a redacted diagnostic report (tool versions, OS, CPU, RAM, error,
  and log tail — with personal names, paths, genotypes, and secrets scrubbed).
  Nothing is sent over the network.
- **Open GitHub issue (`--open`):** Asks for confirmation, then opens a
  pre-filled GitHub issue draft in your browser. No issue is submitted until you
  review and submit it yourself on GitHub.
- In [Advanced mode](modes.md), you can also customize the issue title, number
  of log lines, and maximum URL length.

## Users Workspace vs. Bundle Workspace

WGS Extract keeps your data and the app's internal files cleanly separated:

- **Users Workspace** (default `~/wgs-extract`, or `%USERPROFILE%\wgs-extract`
  on Windows): Holds `config.toml`, your **My Genomes** folder, **Reference
  Data**, **Reference Genomes**, and temporary/log folders. A standalone WGS
  Extract CLI shares this same folder and `config.toml`, so settings and
  libraries stay in sync.
- **Bundle Workspace** (in your OS Application Support / AppData folder): Holds
  the app's internal bundle copy, UI state, and session logs. You do not need to
  move it, and uninstalling removes it while keeping your Users Workspace unless
  you explicitly ask to delete it.

If `~/wgs-extract` already exists and belongs to something else, the app never
overwrites it — it chooses `~/wgs-extract-2` (or the next free name) instead.

## Users Workspace Settings (`config.toml`)

Available in both Basic and Advanced mode, collapsed by default. The
**Users Workspace Settings** card starts with
the users workspace itself. **Current {{usersWorkspace}}** shows where it is;
use **Change {{usersWorkspace}}** to move it (for example, to a larger or
faster SSD):

1. Click **Choose...** (or edit the path) to select a new folder.
2. Click **Move** and confirm. Everything still inside the users workspace
   moves together, and paths inside `config.toml` update automatically.
   Folders you had already moved out of it stay where they are.
3. Click **Use Default** to move back to the default location if desired.

Below that, the card edits your shared `config.toml` directly (see the CLI
[Configuration reference](https://wgse.bio/wgsextract-cli/configuration.html) for every key):

- **Folder paths** (changing any folder asks **Move / Change Setting Only / Cancel**):
  - **Output Directory** (`output_directory`) — Advanced-only override; ignored in Basic mode so Basic runs always use the `WGSEv6/` folder beside each input. Left empty, it shows `{{inputs}}/WGSEv6` as a reminder of that default.
  - **My Genomes Library** (`mygenomes_library`)
  - **Reference Data Library** (`reference_library`)
  - **Reference Genome Library** (`reference_genome_library`)
  - **Temporary Directory** (`temp_directory`) — useful for placing large sorting/calling temp files on a fast scratch drive.
  - **Log Directory** (`log_dir`)
- **Behavior toggles:**
  - **Atomic outputs** (`atomic_outputs`) — writes main outputs to temporary names and renames atomically when finished.
  - **Keep awake** (`keep_awake`) — best-effort sleep prevention during long runs.
  - **Allow missing public-data downloads** (`allow_reference_downloads`, **off by default**) — lets CLI commands fetch missing public references/trees automatically without prompting. See [Privacy and network access](privacy.md) for the exact hosts and request metadata before enabling.

Per-command inputs, such as the reference genome or the input, mother and
father VCFs, are chosen on each command's own page. Haplogrep is part of the
installed CLI and needs no path.
