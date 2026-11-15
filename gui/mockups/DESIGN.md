# WGS Extract redesign: design notes

Status: **exploration for review**, not shipping code. Nothing here changes the
app in `gui/app` or the CLI. Open the mockups with `just mockups` (see
[README.md](README.md)); every page has the annotate tool for feedback.

Contents: [what's wrong today](#1-what-the-current-ui-gets-wrong) ·
[people and jobs](#2-who-its-for) · [principles](#3-principles) ·
[information architecture](#4-information-architecture) ·
[directions](#5-three-directions) · [screens → server API](#6-screens-and-the-server-api) ·
[remote access](#7-remote-access-security-sketch) ·
[accessibility and localisation](#8-accessibility-and-localisation) ·
[open questions](#9-open-questions) · [implementation plan](#10-implementation-plan)

## 1. What the current UI gets wrong

From the shipped bundle (`gui/examples/WGSExtract`), its 12 page files and
about 1,180 English strings, and a screenshot of the Microarray page:

- **Organised by tool and file format, not by what people want.** The sidebar
  is *Info: FASTQ, BAM, VCF* and *Analyze: Extract, Microarray, Ancestry*. A
  person with a Nebula BAM who wants GEDmatch files has to know that
  “microarray” is the word for that.
- **No notion of “my genome”.** Every page starts with its own *BAM/CRAM Input*
  path box and *Reference Genome* dropdown, so the same file and reference are
  chosen again on each page, and nothing remembers what was learned about the
  file (build, coverage, sex, haplogroups).
- **Errors are raw script output.** With the runtime missing, the Microarray
  page shows, inline under two controls, red text such as *“Data source
  scripts/list-reference-genomes.py exited 1: WGS Extract reference catalog not
  found at /Users/…/runtime/…/seed_genomes.csv. Run WGS Extract setup…”*, plus
  a setup banner, plus a disabled Generate button: three signals, no fix.
- **The microarray picker is a 19-checkbox matrix** of chip versions
  (23andMe v3/v4/v5/v3+v5/API, AncestryDNA v1/v2, FTDNA v2/v3, …) with a
  description admitting the builds “are not documented in the files”. People
  know where they want to upload, not which chip version a company used.
- **Mega-pages.** *Extract* has 12 sections, 71 controls and 24 actions:
  extraction, FASTQ alignment, realignment, variant calling, Clair3,
  methylation, typing, phasing and panels. *VCF* has 44 controls. Basic mode
  hides some fields but keeps the shape.
- **Long jobs have no home.** Work runs in a bottom console tab (“Main”):
  no queue, no ETA, no resume after sleep, no notification, nothing to come
  back to tomorrow. 30× genomes are 30–100 GB and realigning takes hours.
- **A generic CLI wrapper.** Pages are JSON descriptions of CLI arguments; a
  dozen-plus platform scripts bridge to the CLI, and dropdowns are filled by
  running scripts, which is slow and fails in the confusing ways above. It
  can’t show progress the CLI doesn’t print, or ask the CLI anything.
- **Spacious and low density.** Large type and padding: seven rows of
  checkboxes fill a laptop screen. The owner prefers compact tables and cards.
- **Desktop only.** Nothing works from a phone, though jobs run for hours.

Meanwhile the CLI grew a typed API and `wgsextract serve`: operations with
JSON Schemas, jobs with progress events, cancellation, rerun with step resume,
result manifests, path guards and a destructive-operation opt-in
([docs/server.md](../../docs/server.md)). The new app should be a client of
that server, not a wrapper around command lines.

## 2. Who it’s for

| | Basic | Advanced |
| --- | --- | --- |
| Who | Bought a 30× genome (Nebula, Dante, YSEQ, Sequencing.com) or has a 23andMe/AncestryDNA raw file | Genetic genealogist, bioinformatics hobbyist, admin of a family’s genomes |
| Has | One BAM/CRAM or FASTQ pair, maybe a VCF; doesn’t know its build | Several samples, multiple builds, long-read data, a GPU or Apple Silicon |
| Top jobs | 1. Make files for GEDmatch/MyHeritage/FTDNA. 2. See Y and mt haplogroups and ancestry. 3. Convert (BAM→CRAM to save space, to FASTQ or VCF for another service). 4. Know if the file is OK. | Realign to T2T/hs38, call variants (DeepVariant, GATK, Clair3), QC (contamination, relatedness), VEP and score annotation, Y/mt extracts for YFull/FTDNA, batch runs, exact options and reproducible commands |
| Needs | Plain words, defaults that are right, time and disk estimates up front, safe long jobs, fixes not errors | Density, every option, the command it maps to, logs, batch, keyboard |

Both groups share the hard parts: reference genomes, huge files, multi-hour
jobs, laptop sleep, Windows/Linux/macOS, and getting runtime dependencies right.

## 3. Principles

1. **Task first, file centred.** Start from what the person wants; every task
   starts from a genome already in the library, with its facts known.
2. **Depth, not a second app.** Basic and Advanced are the same pages; Advanced
   adds options, parameter names, commands, logs and more tools.
3. **Jobs are first-class.** Queue, steps, progress, ETA, cancel, resume,
   notifications, and they survive closing the window or locking the phone.
4. **Preflight, then fix-it.** Check reference, disk, memory and indexes before
   starting, and show any problem as a card with the fix as a button.
5. **Dense and calm.** Compact tables and cards, one primary action per view,
   numbers aligned, little prose.
6. **One codebase, every screen.** The desktop app (Tauri webview, local
   server) and phones/tablets (remote web server) run the same pages.
7. **Private by default.** Genomes never leave the computer; remote access is
   opt-in, local-network only, per-device keys.
8. **Accessible and translatable** from the first component (§8).

## 4. Information architecture

The **genome** (a file in the library and the sample workspace around it) is
the home object. Results, outputs and jobs hang off it; tasks start from it.

```
Desktop sidebar (C)                      Phone tab bar (C)
───────────────────                      ─────────────────
My genomes          ← home               Genomes   (home, file detail, add)
Jobs (2)                                 Tasks     (task list; flows)
Do something                             Jobs (2)
  DNA-test files    (microarray)         Results   (ancestry & lineage)
  Ancestry & lineage                     More      (references, phone access, settings)
  Convert
  Advanced tools    (Advanced only)
Library
  Reference genomes
─────
Phone access ●
Settings
[Basic | Advanced]
Running-job card
Top bar: back link · search & command palette (⌘K) · job ring · connection
```

File detail tabs: Overview (stats, coverage, what we found, what you can do) ·
Results · History (jobs for this file) · Details (Advanced: header, read
groups, server calls).

## 5. Three directions

All three use the same design system and data, so compare structure.

| | A · Guided | B · Console | C · Genome hub (recommended) |
| --- | --- | --- | --- |
| Home | “What would you like to do?” four task tiles, files below | Dashboard: machine status, selectable sample table with bulk actions, queue, live log | Library table with a fix-it card, stats, suggested next steps, jobs |
| Flows | Multi-step wizards | One dense form per operation, every option, CLI and API preview | Single-page task forms with smart defaults; Advanced reveals options in place |
| Navigation | Top nav; phone tabs | Icon rail + command bar | Sidebar (rail on tablet); phone tabs; ⌘K palette too |
| Best for | First-time, one-file users | Experts, batch work | Both, with the file as the anchor |
| Risks | Slow for repeat use; no home for advanced options | Overwhelming for the majority; feels like the old app with better styling | More screens to build; needs a library index the server doesn’t have yet |

**Why C.** Most visits are “do one of four things to my genome”, which A
nails, but people come back to the same files for months and the advanced users
need the depth of B. C gets A’s first-run simplicity (task pages with
defaults, fix-it cards) and B’s depth (Advanced on the same page, palette for
all operations, batch from the library table) while keeping one mental model:
*my genomes, and what I can do with each*. It also maps cleanly onto the CLI’s
sample workspace (`sample/show` already indexes a sample’s facts and outputs).

Pieces worth borrowing whatever is chosen: A’s wizard for first-run setup
(already in C), B’s bulk actions on the library table, B’s per-operation form
generated from the JSON Schema for the Advanced tools page.

## 6. Screens and the server API

Operation names are the server’s (`POST /v1/ops/{op}`; list with
`GET /v1/ops`). Queries (`info`, `info/chip`, `sample/show`, `ref/status`,
`ref/library-list`, `example-genome/list`, `deps/check`) answer at once;
everything else is a job followed with `GET /v1/jobs/{id}/events` (SSE).

| Screen | Reads | Starts | Notes |
| --- | --- | --- | --- |
| First-run setup | `deps/check`, `ref/library-list`, `example-genome/list` | `ref/library` (install), `example-genome/download` | Workspace choice writes config; `ref bootstrap` is CLI-only by design |
| My genomes (home) | `sample/show` per genome, `GET /v1/jobs?status=` | — | **Gap:** no “list the library” op; see below |
| Add files | `info` (query, cached `.wgse_info.json`), `bam/identify` | `info/calculate-coverage`, `bam/index` | **Gap:** browsing allowed folders from a phone |
| File detail | `info`, `sample/show`, `GET /v1/manifest`, `GET /v1/report` | `info/calculate-coverage`, `report` | Coverage chart from `info` (detailed) |
| DNA-test files | `ref/status` | `microarray` (`formats` from the destination picker), `ref/microarray-targets` if missing | Destination → format keys: GEDmatch `all`, MyHeritage `myheritage_v2`, FTDNA `ftdna_v3`, … |
| Ancestry & lineage | manifests of earlier runs | `lineage/y-haplogroup`, `lineage/mt-haplogroup`, `lineage/y-str`, `qc/ancestry` (PR #683), `qc/relate` | K=7/K=12 with 95% intervals from `<stem>.ancestry.tsv` |
| Convert | `ref/status` | `bam/to-cram`, `bam/to-bam`, `bam/unalign`, `bam/to-ubam`, `vcf/snp` + `vcf/indel` | |
| Jobs | `GET /v1/jobs`, `/jobs/{id}/events`, `/jobs/{id}/log?tail=` | `DELETE /v1/jobs/{id}` (cancel), `POST /v1/jobs/{id}/rerun` (resume) | `interrupted` jobs rerun from their last step ([step-resume](../../docs/step-resume.md)) |
| Reference genomes | `ref/library-list`, `ref/status` | `ref/library`, `ref/index`, `ref/verify`, `ref/count-ns`, `ref/clinvar`, `ref/gnomad`, `ref/cadd`, `vep/download`, `vep/verify` | Removing data (`delete` on `ref/*` resources, `vep/delete`) is `destructive`: refused unless the server allows it, and from a phone it needs approval on the computer |
| Advanced tools | `GET /v1/ops/{op}` (JSON Schema → form) | any op, e.g. `realign`, `align`, `vcf/deepvariant`, `vcf/gatk`, `vcf/clair3`, `vcf/sv`, `qc/contamination`, `vep/run`, `vcf/table` | `dry_run` where offered (realign, downloads) for the preview; `GET /v1/variants` for tables |
| Settings | config | — | Folders map to `dataset_library`, `reference_library`, `output_directory`, `server_allowed_paths`; Diagnostics uses `deps/check` and `support` |
| Phone access | — | — | New; §7 |

**Server gaps the mockups assume** (to discuss before building):

1. *Library index:* a query listing genomes with their cached facts (from the
   `dataset_library` sample workspaces), e.g. `library/list`.
2. *Folder browsing* within allowed folders, for adding files from a phone.
3. *System status:* free disk per volume, CPU/RAM load, GPU, for preflight and
   the dashboard.
4. *Preflight as data:* the estimates `realign` and friends print
   ([resource_estimates.md](../../docs/resource_estimates.md)) returned as JSON
   before a job starts (e.g. extend `dry_run`).
5. *Structured progress:* step names and fractions in `progress` events so the
   multi-stage bar is honest.
6. *Error codes with fix hints* in manifests (e.g. `missing_index`,
   `reference_not_installed`) so fix-it cards needn’t parse messages.
7. *Remote listener* (§7): the HTTP server deliberately listens on 127.0.0.1
   only today.

## 7. Remote access: security sketch

Goal: watch and start jobs from a phone on the same network without making
the analysis server reachable from the network.

- **Shape.** The desktop app runs a separate *remote gateway* (off by default)
  that listens on the LAN with TLS and forwards an allow-listed subset of the
  API to the local server. The local server stays 127.0.0.1-only, unchanged.
- **Pairing.** The desktop shows a QR code with the URL, a one-time code
  (5 minutes) and the gateway’s self-signed certificate fingerprint. The phone
  opens the URL, shows the same 6-digit code, and the desktop asks “Allow
  Mac’s iPhone?”. Approval issues a per-device key (HTTP-only, secure cookie).
- **Scope per device.** View; start and cancel jobs (toggle); never delete
  (`destructive` requests become “approve on the computer” prompts); never
  change settings, folders or remote access; no file upload or download of
  genome data; folder browsing limited to allowed folders.
- **Exposure.** Local network only; for away-from-home use, a private network
  such as Tailscale. No port forwarding, no cloud relay. Turns itself off after
  7 days without a paired device connecting.
- **Visibility.** A connection indicator in the desktop sidebar; an access log
  (who did what); remove a device to revoke its key immediately.
- **Web constraints.** Push notifications to a phone need a service worker and
  a trusted origin; with a self-signed certificate we may need an installable
  PWA, or fall back to in-page alerts. Open question.

## 8. Accessibility and localisation

- WCAG 2.2 AA: text ≥ 4.5:1 in both themes (verified with axe-core on every
  mockup page, light and dark), focus rings everywhere, 44 px touch targets on
  phones and coarse pointers, reduced motion honoured.
- Keyboard: Tab order follows the layout, Escape closes overlays, arrows move
  in tabs and the palette, ⌘K/Ctrl+K opens the palette.
- Screen readers: landmarks, labelled icon buttons, progress bars with values,
  a live region for toasts, text alternatives for every chart.
- Localisation: no text in icons or images; layouts tolerate 40% longer
  strings; locale-aware numbers, sizes and dates; haplogroup names and file
  names stay as data. The current app’s `strings/*.toml` approach carries over.

## 9. Open questions

For Randy and theontho (also pinned as notes on the pages, by “AI”):

1. Direction: C as recommended, or something else? Which pieces of A and B?
2. Is “DNA-test files” the right name for microarray export? Alternatives:
   “Upload files”, “Raw data for DNA sites”, “Microarray kits”.
3. Destination picker: are the upload claims right (GEDmatch, MyHeritage,
   FTDNA, Living DNA, Nucleus accept uploads; 23andMe and AncestryDNA don’t)?
   Which chip versions should each destination default to?
4. Should Basic users see haplogroup paths, or just the result and a one-line
   explanation?
5. Ancestry: show K=7 only in Basic and K=12 in Advanced? How prominent should
   the “not an ethnicity” note be?
6. Library: genomes are files today. Should the app group a person’s files
   (BAM + VCF + kit) under one “person”?
7. Remote access: is LAN-only enough, or is a hosted relay worth its privacy
   cost? Is per-device “can start jobs” the right granularity?
8. Jobs: run one at a time by default (`server_max_jobs` 1) and queue the rest?
9. Logo: A, B or C?
10. Where does the desktop app live while jobs run: keep running in the menu
    bar/tray when the window closes?

## 10. Implementation plan

After the mockups are agreed. Each phase ships behind the current app until it
can replace it.

1. **Tokens and shell.** Port `design-system/` into the app as CSS (no build
   step needed); the shell with sidebar/rail/tab bar via container queries;
   theme, density, Basic/Advanced; the icon set as an SVG sprite. Typed
   client for the server (generated from `GET /v1/openapi.json`).
2. **Library and jobs.** Server: library index, system status, structured
   progress (gaps 1, 3, 5). App: home, add file, file detail, jobs centre with
   SSE, notifications, resume.
3. **Basic flows.** DNA-test files, ancestry & lineage, convert, reference
   genomes, first-run setup, fix-it cards from error codes (gaps 4, 6).
4. **Advanced.** Schema-generated operation forms with CLI/API preview,
   command palette over all operations, batch actions on the library.
5. **Phone.** Remote gateway, pairing, device scopes, PWA and notifications
   (gap 7); test on iOS Safari and Android Chrome.
6. **Retire the wrapper.** Remove the page JSON, data-source scripts and
   bundle runtime glue the server replaces; keep `strings/` for translations.
