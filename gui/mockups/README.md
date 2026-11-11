# WGS Extract UI mockups

Interactive HTML mockups, a design system and an icon set for a redesigned
WGS Extract app: one client of the `wgsextract serve` API that works as the
desktop app and on phones and tablets. **Status: exploration for review. This
is not shipping code**; nothing here is used by `gui/app` or the CLI.

The reasoning (what’s wrong today, personas, the three directions, the
screen → API mapping, remote-access security, the build plan) is in
[DESIGN.md](DESIGN.md).

## Open it

**Online, nothing to install:**
<https://wgsextract.github.io/wgsextract-cli/app/mockups/> (published by the
GitHub Pages workflow from `main`). There, notes are saved in your browser:
Export the JSON from the Notes panel and commit it to
`gui/mockups/feedback/`, or open the mockups locally (below) to save notes
straight into the repo. Notes already committed are shown on the published
pages too.

**Locally:**

```sh
just mockups                        # from anywhere in the repo
python3 gui/mockups/serve.py        # the same, without just
python3 gui/mockups/serve.py --open # and open the browser
```

It prints an address such as `http://127.0.0.1:8765/` (the first free port
from 8765; `--port` picks one). `serve.py` needs only Python 3.9+ and its
standard library, no other installs.

You can also open `index.html` straight from disk. Everything works except
saving notes to the repo: they stay in that browser (see below).

## Review and leave notes

Every page has a dark bar on top that is not part of the design:

| Control | Does |
| --- | --- |
| ← Mockups | back to the index |
| A / B / C | which direction the page belongs to |
| Fit · Phone · Tablet · Desktop | shows the page at 390×844, 820×1180, 1440×900, or the window size |
| ☾ / ☀ | light or dark theme |
| Annotate (count) | annotate mode, with the number of open notes; key **A** |
| Notes | the list of notes on the page; key **N** |

The app’s own **Basic / Advanced** switch is at the bottom of the sidebar (or in
Settings on a phone). Most buttons do something small: open a dialog, show a
toast, switch a view. Choices you make are remembered across pages.

In annotate mode, click any element, or drag a box over an area, and write a
note. Pick who you are once (theontho, Randy or another name); the browser
remembers it. From the keyboard: Tab to an element and press Enter. Pins show
where each note is and follow their element when the window or viewport
changes; a note on something hidden in the current view says so in the list.

In the Notes panel you can reply, resolve or reopen, filter open/resolved/all,
jump to a note’s element, add a whole-page note, and Export/Import JSON.

## How notes travel through git

With `serve.py` running, notes are written to
`gui/mockups/feedback/<page-id>.json`, one file per page, as they are made.
Commit them like any other change:

```sh
git add gui/mockups/feedback && git commit -m "Mockup notes" && git push
```

The other person pulls, runs `just mockups`, and sees the notes pinned in place,
replies to them, and commits again. The files are pretty-printed with sorted
keys, and notes are keyed by random ids, so two people adding notes to the same
page usually merge without conflict. If git does report a conflict in a
feedback file, keep both sides’ notes, then run
`python3 gui/mockups/serve.py --check`, which validates and tidies every
feedback file.

Without the server (the published site, or a page opened from disk), notes
live in the browser’s local storage, and a yellow banner in the Notes panel
says so. The published site also shows the notes committed in `feedback/`
(the Pages workflow lists them in `feedback/_pages.json`). Use **Export
JSON** and save the file as `gui/mockups/feedback/<page-id>.json`; **Import
JSON** merges a committed file into the browser so you can see others’ notes.

A note looks like this:

```json
{
  "anchor": {"annot": "start", "label": "button “Convert”", "path": "", "x": 0.42, "y": 0.5},
  "author": "Randy",
  "context": {"depth": "basic", "theme": "dark", "viewport": "phone"},
  "created": "2026-11-11T17:02:11Z",
  "id": "n3fa9c1d2e4",
  "replies": [{"author": "theontho", "created": "…", "id": "r…", "text": "Agreed"}],
  "status": "open",
  "text": "Should this say Start?",
  "updated": "2026-11-11T17:05:40Z"
}
```

`anchor.annot` is the nearest `data-annot-id` on the page, `path` a CSS path
below it, and `x`/`y` (and `w`/`h` for a box) fractions of that element. Notes
by “AI” are questions from the designer about decisions to make.

## Folder layout

```
gui/mockups/
  index.html            gallery of directions and screens, overall notes
  DESIGN.md README.md
  serve.py              static server + notes API (stdlib only)
  feedback/             <page-id>.json notes, committed
  design-system/
    wgse.css            link this: imports tokens, base and components
    tokens.css base.css components/*.css
    design-system.html  every token and component, light and dark
  icons/
    wgse/*.svg          WGS Extract icons (drawn for this project)
    lucide/*.svg        Lucide UI icons (ISC, see lucide/LICENSE)
    logos/*.svg         three app logomark options
    build_icons.py      bundles the SVGs into icons.js (run after edits)
    icons.js icons.html
  directions/
    a/                  Guided: home, microarray wizard
    b/                  Console: dashboard, microarray batch form
    c/                  Genome hub (recommended): every screen
  shared/
    boot.js             loads the runtime below in order
    core.js theme.js icons.js ui.js palette.js chrome.js start.js
    data/               illustrative genomes, jobs, catalogs
    render/             data-driven blocks (tables, jobs, charts, shell)
    shells/             each direction’s navigation
    annotate/           the notes tool (store, anchors, pins, panel)
    mockup.css docs.css
  thumbs/               small screenshots for the index
```

No build step, no external fonts or CDNs: plain CSS and classic scripts, so the
pages also work offline and from `file://`.

## Add or change a mockup

1. Copy a page in the same direction, say `directions/c/convert.html`, and
   change `data-page-id` (unique, `<direction>-<name>`), `data-title` and
   `data-nav` on `<body>`. Keep the two stylesheet links and `boot.js`.
2. Write the content inside `<div class="page">` with the design-system
   classes; see `design-system/design-system.html`. Icons are
   `<i data-icon="name"></i>` with names from `icons/icons.html`.
3. Put a `data-annot-id` on each meaningful part (sections, cards, key
   buttons) so notes stay attached when the markup changes. Don’t rename ids
   that have notes.
4. Reuse data blocks instead of copying markup, e.g.
   `<div data-render="library-table">`, `data-render="jobs"`,
   `data-render="destinations"` (see `shared/render/`).
5. Interactions are attributes, not page scripts: `data-open="#dialog"`,
   `data-toast="…"`, `data-seg` with `data-when`, tabs with `role="tab"`
   (see the top of `shared/ui.js`).
6. Link it from `index.html`. Root ruff/pytest and the app’s lint skip this
   folder; check pages in a browser at phone and desktop sizes.

Sample data is illustrative: names follow the GIAB HG002 trio, and sizes,
coverage and haplogroups are realistic-looking, not measured.
