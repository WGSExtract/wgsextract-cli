# WGS Extract GUI Architecture

WGS Extract GUI is a single TypeScript application — a Web UI and a local Node
backend — packaged as a native desktop app for Windows, macOS, and Linux with
[Tauri](https://tauri.app).

## Runtime layers

1. **Shared TypeScript (`gui/app/shared/`)**: Bundle manifest types, UI mode
   filtering (`basic` vs `advanced`), localization tables, workspace path
   formatting, result-document parsing, and documentation URL helpers.
2. **Local Node backend (`gui/app/web/src/server/`)**: Loads and validates
   bundles against [`devdocs/schema/`](schema/), runs setup scripts and CLI
   actions, manages the local job server ([`job-server.md`](job-server.md)),
   checks prerequisites ([`prerequisites.md`](prerequisites.md)), reads/writes
   `config.toml`, and serves the Web UI over an authenticated loopback socket
   (`127.0.0.1` with a per-launch token). Packaged installers embed Node so end
   users do not need a separate Node installation.
3. **Browser Web UI (`gui/app/web/src/client/`)**: Renders pages, forms,
   streaming terminal output, result cards, the built-in Genome Viewer
   (`igv.js`), and the offline Gene Page.
4. **Tauri desktop shell (`gui/app/web/packagers/tauri/`)**: Native window,
   application menus, native file/folder pickers, OS installers, and signed
   self-updates ([`distribution.md`](distribution.md)).

The app keeps two workspaces cleanly separated:

- **Users Workspace (`{{usersWorkspace}}`, default `~/wgs-extract`)**: Shared
  with the standalone CLI (`config.toml`, `mygenomes/`, Reference Data,
  Reference Genomes, temp/logs). Movable to another drive from Settings or
  library pages.
- **Bundle Workspace (`{{bundleWorkspace}}`)**: App-managed bundle copy, saved
  UI state, and run logs in the OS Application Support / LocalAppData folder.

## Privacy and network boundary

WGS Extract serves two audiences with different expectations:

- **CLI / automation / air-gapped users** expect a deterministic, strictly
  offline engine that never opens a socket unless explicitly instructed.
- **Casual (Basic mode) desktop users** expect a helpful app that tells them
  when updates exist and offers to download missing reference genomes, gene
  maps, or tools with a single click instead of leaving them stuck on a CLI
  error.

We resolve this tension with a **two-layer consent boundary** rather than
weakening the CLI's offline guarantees:

### Layer 1 — CLI back-end (`wgsextract`): strictly offline by default

- Ordinary analysis, inspection, and reporting commands **never** make network
  requests, never check for updates, and never install missing tools or data
  implicitly.
- Runtime Pixi launches always pass `--no-install --frozen`.
- Network access in the CLI happens *only* in:
  1. Explicit install/download subcommands (`deps install`, `ref library --install`, `ref bootstrap`, `ref gene-map`, `vep download`, etc.); or
  2. Explicit opt-in flags/settings (`--download`, `--online`, `--remote-sizes`, or `allow_reference_downloads = true`).
- This contract keeps scripts, servers, MCP clients, and air-gapped machines
  safe by construction. See [`docs/privacy.md`](../../docs/privacy.md).

### Layer 2 — Desktop GUI front-end: helpful, transparent, and controllable

The desktop app sits *above* the CLI and invokes the CLI's explicit commands on
the user's behalf. On a fresh install, it makes no outbound requests before
the first-launch network choice. **OK** starts setup if needed and enables
**Check for updates at launch**, which is off by default. **Not now** opens
**Settings → Network & Privacy** without contacting an external server.
After that choice, the app distinguishes four kinds of network activity:

| Tier | Policy | Examples |
| --- | --- | --- |
| **1. Automatic connections** | **Privacy mode** in **Settings → Network & Privacy** pauses automatic activity. Setup downloads public files; launch checks require the separate **Check for updates at launch** setting and fetch release metadata only. Neither sends sample data. See [GUI Privacy](../docs/privacy.md) for IP and HTTP-header visibility. | First-run setup after **OK**; app release checks at launch. Catalog update checks are planned, not automatic today. |
| **2. Interactive missing-prerequisite offers** | Never downloads silently. Before running an action (or opening the Gene Page), the GUI checks local prerequisites offline (`wgsextract ref needs`), stops if required public data is missing, and offers **Cancel / Continue / Install and Continue**. Downloading occurs only on click, via the CLI's explicit install command. | Missing CRAM reference genome; missing RefSeq `gene-map` on Gene Page; missing annotation database for a VCF annotation action. |
| **3. Explicit management & help actions** | Runs only when the user clicks an explicit button or link. | **Run Setup**, **Install / Download** on Reference Data / Reference Genomes / Tools, confirmed **Look up sizes**, **Open GitHub issue** (redacted & confirmed), clicking rendered documentation links (`https://wgse.bio/...`). |
| **4. Sample-derived network operations** | **Never default-on** in Basic or Advanced mode. Always requires explicit per-action user confirmation naming the destination host and the sample-derived data (paths/byte ranges) that will be transmitted. | Remote Genome Viewer (**Connect and view remote file**); remote alignment URLs (`realign --input https://...`); any future remote variant/position query. |

### Design rules for new GUI features

1. **Never bypass Layer 1 silently:** The GUI must never inject `--download` or
   flip `allow_reference_downloads = true` without explicit user consent.
2. **Gate automatic connections:** Before the first-launch choice, make no
   outbound requests. Afterward, honor **Privacy mode** for all automatic
   activity and the launch-check setting for app release checks. Future
   catalog update checks must use metadata only and must not fetch the update
   itself.
3. **Never send sample metadata in update or catalog checks:** These checks
   must never include filenames, local paths, sample IDs, builds inferred from a
   user's BAM/VCF, or genomic coordinates.
4. **Clickable documentation links are user-initiated:** Error messages and
   terminal output may render `https://wgse.bio/wgsextract-cli/...` links so
   users can open formatted HTML help in one click, but the app never prefetches
   those pages in the background.
