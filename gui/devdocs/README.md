# WGS Extract GUI — Developer Documentation

This folder (`gui/devdocs/`) contains developer, bundle-author, and packager
documentation for WGS Extract GUI.

> **End users:** Looking for how to use the desktop app? See the
> **[User Documentation](../docs/README.md)**
> ([rendered web docs](https://wgse.bio/wgsextract-cli/app/docs/index.html)).

## Architecture and design

| Guide | Contents |
| --- | --- |
| [Architecture](architecture.md) | App runtime layers (shared TS, local Node backend, Tauri shell) and the **CLI vs. Desktop App privacy & network boundary** |
| [Post-v0.6.3 feature details](wgsextract-post-v063.md) | Workflow controls, platform constraints, and feature notes for the WGSExtract bundle |

## Setup, packaging, and site

| Guide | Contents |
| --- | --- |
| [Developer setup](developer_setup.md) | Toolchain (`uv`, Node, Rust), OS prerequisites, `make` / `just gui-*` targets, and PR loop |
| [Distribution](distribution.md) | Tauri installers, macOS signing & notarization, Windows/Linux packaging, updater feed, and uninstall hooks |
| [Site generation](site_generation.md) | Editing `gui/site_src/` and regenerating `gui/site/` |

## Bundle runtime contracts

| Guide | Contents |
| --- | --- |
| [JSON Schemas](schema/) | `manifest.schema.json` and `page.schema.json` validated on every bundle load |
| [Data sources](data-sources.md) | Dynamic tables/dropdowns and JS transform modules |
| [Job server](job-server.md) | Running cancellable actions through a bundle's local HTTP job server |
| [Prerequisites](prerequisites.md) | Detecting missing reference genomes/data before running and offering to install them |
| [Result documents](result-documents.md) | Consuming `wgsextract.result/1` JSON manifests, warnings, metrics, and HTML result cards |
