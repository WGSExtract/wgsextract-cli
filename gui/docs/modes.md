# Basic and Advanced mode

The **Basic / Advanced** switch sits at the top of the left sidebar. It controls
how much of the desktop interface is shown without changing the underlying WGS
Extract CLI engine.

## Basic mode (default)

New installs start in **Basic** mode. Basic mode is designed for everyday
genomics tasks with safe defaults:

- **Focused controls:** Only common inputs, formats, and actions are shown.
  Rarely needed flags, tuning options, and raw configuration editors are hidden.
- **Hidden settings cannot surprise you:** When you run an action in Basic mode,
  any non-path control or checkbox option that is hidden in Basic mode is run at
  its **default value** — even if you previously changed that option while in
  Advanced mode. Your saved Advanced value is preserved for when you switch back
  to Advanced mode, so experimenting in Advanced mode never silently alters a
  Basic run.
- **Automatic output folders:** Outputs are written to a `WGSEv6/` folder beside
  the primary input file (or beside an input already inside a `WGSEv6/` folder),
  keeping your original lab folder untouched except for standard sidecar indexes
  (`.bai`, `.crai`, `.tbi`, `.csi`). Machine-readable manifests and info caches
  go in `WGSEv6/manifests/`.
- **Expanded sections:** Everyday sections open directly unless a section is
  explicitly collapsed by default (such as **Report an Issue** on Settings).

## Advanced mode

Switch to **Advanced** mode whenever you need full control over CLI options or
workspace settings:

- **Every user option exposed:** Reveals advanced-only sections, dropdowns,
  checkboxes, and fields across pages — for example, aligner index choice on
  [Reference Genomes](reference-genomes.md), VEP cache mirror/version overrides
  and custom URL downloads on [Reference Data](reference-data.md), and the
  **Users Workspace Location** and **Users Workspace Settings (`config.toml`)**
  editors on [Settings](settings.md).
- **Output Directory override:** Only Advanced mode lets you set a custom
  **Output Directory** override in the GUI. Switching back to Basic mode hides
  and ignores that GUI override without erasing it. (Note: if you save an
  `output_directory` in `config.toml`, standalone CLI commands run from a
  terminal still read `config.toml`.)
- **Collapsible sections:** In Advanced mode, titled sections start collapsed to
  their header rows (except always-open sections such as **Inputs**) so pages
  with many options remain easy to scan. Click a section's header or chevron to
  expand or collapse it.
- **Detailed action titles:** Some actions display a more specific technical
  title in Advanced mode when one is defined.

## Switching modes

You can switch between **Basic** and **Advanced** at any time from the top of
the sidebar:

- Your selected mode is saved automatically in the app's settings and restored
  on next launch.
- Input file paths and library folder paths are shared between modes.
- Moving a library folder from the **My Genomes**, **Reference Data**, or
  **Reference Genomes** pages works in **both** Basic and Advanced mode.
