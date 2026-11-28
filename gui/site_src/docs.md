---
output: 'docs.html'
title: 'WGS Extract GUI documentation map'
description: 'Documentation map for WGS Extract GUI: the app, bundles, distribution, and static site generation.'
eyebrow: 'Documentation'
heading: 'Where to go next.'
lede: 'Start with v0.9.0 downloads or one-command developer setup. Workflow details, distribution, and schemas live close to the code.'
actions: 'User Documentation|docs/index.html|primary; Developer Docs|devdocs/index.html; GUI Architecture|architecture.html'
footer_title: 'Documentation map'
footer_text: 'End-user guides live in gui/docs/; developer and bundle docs live in gui/devdocs/.'
---

::: section
::: wrap
::: grid two
::: card
{{ kicker: End users }}
### User Documentation
Guides for Basic and Advanced mode, Reference Data, Reference Genomes, Tools, Settings, Privacy, the built-in Genome Viewer, and the Gene Page.

{{ link: Open User Docs|docs/index.html|inline-link }}
{{ link: Privacy & network access|docs/privacy.html|inline-link }}
:::

::: card
{{ kicker: Product }}
### Developer README
The `gui/README.md` describes the app layout, setup, common commands, bundles, workspaces, and distribution packaging.

{{ link: Open gui/README|readme.html|inline-link }}
{{ link: Developer Docs Index|devdocs/index.html|inline-link }}
:::

::: card
{{ kicker: Development }}
### One-command setup
Clone the repo and run `make setup` on Linux/macOS, or `powershell -ExecutionPolicy Bypass -File .\make.ps1 setup` in Windows PowerShell 5.1. Without make on Linux/macOS, use `sh scripts/dev_setup.sh`.

Setup prepares missing user-level uv, Node, and Rust tools, locked dependencies, hooks, and a real Web UI smoke check. It never elevates or installs system packages; the guide lists manual desktop prerequisites. No sibling WGSExtract CLI checkout or genome download is needed for UI development: start with `examples/GitHelper`.

{{ link: Open developer setup|devdocs/developer_setup.html|inline-link }}
:::

::: card
{{ kicker: Releases }}
### Current app and CLI: v0.9.0
Each WGSExtract CLI release carries the app's installers and the CLI it bundles, with one set of release notes. App releases up to v0.6.6 stay on the former gui-for-cli repository. The changelog tracks the move to one Tauri app, native Windows setup, macOS notarization, and bundle improvements.

{{ link: Open releases|https://github.com/WGSExtract/wgsextract-cli/releases|inline-link }}
{{ link: Open changelog|docs/CHANGELOG.html|inline-link }}
:::

::: card
{{ kicker: WGSExtract }}
### Current workflows
Nanopore handling in existing actions, Clair3 and methylation controls, plus opt-in MultiQC, expanded microarray/AADR exports, targeted Y analysis, and resource requirements. See what is in the app and what is still CLI only.

{{ link: WGSExtract workflows|wgsextract.html|inline-link }}
{{ link: Post-v0.6.3 feature details|devdocs/wgsextract-post-v063.html|inline-link }}
:::

::: card
{{ kicker: Schemas }}
### Bundle schemas
`manifest.json` and page files are described by JSON Schemas in `gui/devdocs/schema/`.

{{ link: Open schemas on GitHub|https://github.com/WGSExtract/wgsextract-cli/tree/main/gui/devdocs/schema|inline-link }}
:::

::: card
{{ kicker: Archive }}
### Earlier experiments
The SwiftUI app, Swift CLI, prototype renderers, and benchmark reports live on the `old-experimental` branch.

{{ link: Open the archive branch|https://github.com/WGSExtract/gui-for-cli/tree/old-experimental|inline-link }}
:::

::: card
{{ kicker: Distribution }}
### Packaging
Signing, notarization, Tauri packages for each OS, release artifacts, self-updates, and embedded bundle defaults.

{{ link: Open distribution doc|devdocs/distribution.html|inline-link }}
:::
:::
:::
:::

::: section
::: wrap
## Generate this site

Edit Markdown in `site_src/`, then run:

```bash
python3 scripts/validate_site_sources.py
python3 scripts/build_site.py
open site/index.html
```

The dependency-free generator writes HTML to `site/` for GitHub Pages. Commit regenerated HTML alongside source changes; `make site` is also available.

{{ link: Site generation guide|devdocs/site_generation.html|inline-link }}
:::
:::
