# Settings

The **Settings** page (at the bottom of the sidebar) controls initial CLI setup,
appearance and language, bug reporting, and — in [Advanced mode](modes.md) — your
workspace location and `config.toml` preferences.

## Setup

The **Setup** card at the top of Settings installs and verifies the bundled
WGS Extract CLI and its core bioinformatics tools:

- **Run Setup / Rerun Setup:** Installs the locked WGS Extract CLI runtime into
  the app's data folder and verifies its core dependencies. You can cancel a
  running setup at any time.
- **Step list:** Expands automatically before first setup, while setup is
  running, or if a step fails; collapses cleanly once setup succeeds (click the
  chevron to inspect steps anytime).
- **Open Bundle Workspace:** Opens the app's internal bundle workspace folder
  (run logs and saved UI state) in Finder / File Explorer.

> **Windows note:** Setup installs Pixi tool environments under
> `%LOCALAPPDATA%\WGSExtractPixi` for the current Windows user account. If you
> switch Windows user accounts or delete that folder, rerun **Setup**.

## Standard Options (Appearance and Language)

Always visible in both Basic and Advanced mode:

- **Language:** Defaults to **Use system default** (matching your OS language).
  You can switch explicitly among available languages; AI-translated locales are
  labeled clearly in the dropdown.
- **Icon Set:** Choose between **Bootstrap Icons** (crisp vector icons) and
  **Emoji** icons.
- **Color Theme:** Follow your OS (**System**), or force **Light** or **Dark**
  mode.
- **Web Font:** Choose the default system font priority or prefer **SF Pro**
  when available.

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

## Users Workspace Location (Advanced mode)

In [Advanced mode](modes.md), the **Users Workspace Location** card lets you
move your entire Users Workspace (for example, to a larger or faster SSD):

1. Click **Choose...** (or edit the path) to select a new folder.
2. Click **Move** and confirm. Everything inside the Users Workspace moves
   together, and paths inside `config.toml` update automatically. Folders you
   had already pointed elsewhere stay where they are.
3. Click **Use Default** to move back to the default location if desired.

## Users Workspace Settings (`config.toml`, Advanced mode)

In [Advanced mode](modes.md), the **Users Workspace Settings** card edits your
shared `config.toml` directly (see the CLI
[Configuration reference](https://wgse.bio/wgsextract-cli/configuration.html) for every key):

- **Folder paths** (changing any folder asks **Move / Change Setting Only / Cancel**):
  - **Output Directory** (`output_directory`) — Advanced-only override; ignored in Basic mode so Basic runs always use the `WGSEv6/` folder beside each input.
  - **My Genomes Library** (`mygenomes_library`)
  - **Reference Data Library** (`reference_library`)
  - **Reference Genome Library** (`reference_genome_library`)
  - **Temporary Directory** (`temp_directory`) — useful for placing large sorting/calling temp files on a fast scratch drive.
  - **Log Directory** (`log_dir`)
- **Behavior toggles:**
  - **Atomic outputs** (`atomic_outputs`) — writes main outputs to temporary names and renames atomically when finished.
  - **Keep awake** (`keep_awake`) — best-effort sleep prevention during long runs.
  - **Allow missing public-data downloads** (`allow_reference_downloads`, **off by default**) — lets CLI commands fetch missing public references/trees automatically without prompting. See [Privacy and network access](privacy.md) for the exact hosts and request metadata before enabling.
- **Default inputs & tools:**
  - Default **Reference FASTA**, default **Input VCF**, **Mother VCF** / **Father VCF** (for trio workflows), and custom **Haplogrep** executable path.
