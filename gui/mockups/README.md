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

### Handing notes to an AI agent

Every feedback file starts with an `"about"` field: a fixed paragraph that
tells any AI agent what the file is (review notes on these mockups, in this
repository and branch), what the fields mean, and how to act on them (read
this README and DESIGN.md, change the mockups for each open note, keep the
design system consistent, reply in the thread and mark it resolved). It is
written by `serve.py` and by **Export JSON**, and it is the same in every
file, so it never shows up in diffs; exports from a branch preview name that
branch instead of `main`. Import ignores it.

So Randy (or anyone) can annotate on the published site, press **Export
JSON**, and give the downloaded file to their agent with “apply these notes”;
the file explains itself. The text lives in `shared/annotate/about.js`.

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

## The iteration loop

1. **Annotate** on the published site or locally (`just mockups`).
2. **Save the notes to the repo:** locally they are already in
   `gui/mockups/feedback/`; from the published site, Export JSON and commit it
   there (or hand it straight to an agent, above).
3. **Change the mockups on a `mockups/<name>` branch** of this repository
   (an agent or a person), replying in each note’s thread, and push it.
4. **Review the preview** at
   `https://wgsextract.github.io/wgsextract-cli/app/mockups/preview/<name>/`,
   up a few minutes after each push. `<name>` is the branch name after
   `mockups/`, lower-cased, with anything but letters, digits and `-` turned
   into `-` (`mockups/Randy-nav` → `randy-nav`). All previews are listed at
   <https://wgsextract.github.io/wgsextract-cli/app/mockups/preview/>. A yellow
   banner says which branch and commit you’re looking at. Notes there stay in
   your browser; Export them and commit them to the same branch.
5. **Open a PR from the branch to `main` and merge it.** That publishes the
   change at <https://wgsextract.github.io/wgsextract-cli/app/mockups/>.
   Deleting the branch removes its preview.

How it’s built: pushing or deleting a `mockups/*` branch runs
`.github/workflows/mockup_previews.yml`, which only starts the Pages workflow
(`pages.yml`) on `main`; the `github-pages` environment deploys from `main`
alone. That workflow runs `scripts/build_mockup_previews.py` from `main`,
which copies just the static files (HTML, CSS, JS, JSON, images, Markdown)
under `gui/mockups/` of each branch, read from `git archive` and never run.
Only branches of this repository count, so only people with write access can
publish a preview; forks can’t. Pages also republishes on every push to
`main` that touches `gui/mockups/**`. If something didn’t update, start it by
hand: `gh workflow run pages.yml --ref main`.

Which iteration is this? The footer of each mockups index names the branch
(or `main`), commit and date it was published from; a checkout says “Local
copy”. The Pages build writes that into `shared/version.js` in the published
copies only.

## Hosted review server (randy-mini)

The shared review space is **not GitHub Pages**:

- Tailnet: <http://randy-mini.tail720e6b.ts.net:8765/>
- Randy's LAN: <http://10.1.10.48:8765/>
- All notes: <http://randy-mini.tail720e6b.ts.net:8765/notes.html>
  (or `/notes.html` at the LAN address)
- Ideas: `/preview/`, with `mockups/<name>` at `/preview/<name>/`.

Both addresses use **the same database**. Notes, replies and resolutions save
immediately; refresh another browser or reopen its Notes panel's page to see
changes. All notes has idea, page, author and status filters, in-place note
links, Export all, and Import. Imports merge by note id and newer `updated`
timestamps: importing the same export twice does not duplicate notes or replies.
Exports include the self-describing `about` text and a `pages` array, each
with its `idea` (`main` or the full `mockups/*` branch name), `page`, `title`,
`notes` and server-relative `url`. The full branch name is the stable identity:
colliding URL slugs may change order after pushes, but notes never change ideas. Existing
committed notes were imported once into `main`. Notes on deleted preview
branches remain in the database/export even though their pages are no longer
available. Author names are self-selected, not authenticated.

The server accepts socket peers only from loopback, RFC1918, tailnet
`100.64.0.0/10`, IPv6 loopback and ULA. Forwarding headers are ignored.
POSTs with an Origin must match the request's explicitly allowed host and port.
Do not forward this port to the Internet; there are no accounts or TLS.
`file://` and GitHub Pages still use browser-local notes, not this database.

### Run the shared store locally

```sh
python3 gui/mockups/serve.py --db out/mockups/notes.db
# Optional read-only static snapshots from a bare clone:
git clone --bare https://github.com/WGSExtract/wgsextract-cli out/mockups/repo.git
python3 gui/mockups/serve.py --db out/mockups/notes.db --repo out/mockups/repo.git
```

`--refresh-minutes 5` is the default. The fetch worker reads `main` and
`mockups/*` from the bare clone's `origin`; snapshot files are extracted
with the Pages publisher's exact branch naming and static allow-list, never
executed. Cache directories are keyed by commit and idea; the previous
snapshot is retained briefly for in-flight requests. Shared review tooling
is served from the running app so older ideas also get the shared API.
Without `--db`, the original per-page `feedback/*.json` workflow is unchanged.
For additional listening authorities use repeatable `--allow-host HOST:PORT`.

### Export back to git

Run on the host (or copy a backup to a local `out/` folder first):

```sh
python3 gui/mockups/serve.py --db /Users/wgse/mockup-server/notes.db \
  --export-feedback /Users/wgse/mockup-server/export
```

`export/<page>.json` belongs in main's `gui/mockups/feedback/`.
`export/preview/<slug>-<branch-hash>/<page>.json` belongs in the branch named
by the document's `idea`, in its
`gui/mockups/feedback/`. Copy only the relevant idea, review the diff, commit
and push; never commit the live DB. **Export all** is a portable JSON download,
not per-page files. `--import-feedback DIR` imports only `DIR/*.json` into
main and exits; do not schedule it (it could resurrect deliberately deleted
notes). `/api/import` accepts an all-notes export, `/api/export` returns it,
and `/api/notes` lists flattened notes with optional `idea`, `page`, `status`
and `author` query filters. Per-page `/api/feedback/<page>?idea=mockups%2F<branch>`
retains the add/reply/status/edit/delete/merge operations.

### Reproduce the unattended setup

Everything except the launch agent plists lives in
`/Users/wgse/mockup-server/`, as `wgse`; no sudo, GUI login, reboot or
changes to `/Users/randy` are needed. Python 3.9+ and git must already exist.
On randy-mini:

```sh
mkdir -p /Users/wgse/mockup-server
cd /Users/wgse/mockup-server
git clone --bare https://github.com/WGSExtract/wgsextract-cli repo.git
git clone --branch main https://github.com/WGSExtract/wgsextract-cli app
/usr/bin/python3 app/gui/mockups/serve.py --db notes.db \
  --import-feedback app/gui/mockups/feedback
/usr/bin/python3 app/gui/mockups/hosting/install.py
```

Before the implementation PR merges, clone branch `feat/776-mockup-server`
instead of main. The update job fetches every five minutes and fast-forwards
the app checkout, refusing local edits. Once its HEAD is contained in main
(or GitHub reports its PR merged, including squash merges), it switches
to main. It restarts the server whenever the app commit changes.
For a manual switch after merge:

```sh
git -C /Users/wgse/mockup-server/app fetch origin main
git -C /Users/wgse/mockup-server/app checkout -b main FETCH_HEAD
launchctl kickstart -k user/502/org.wgsextract.mockups
```

`hosting/server.plist.template` and `hosting/install.py` install three jobs
in `~/Library/LaunchAgents`, bootstrapped into **user/502**, not gui/502:

- `org.wgsextract.mockups`: RunAtLoad + KeepAlive, port 8765, low-priority I/O.
- `org.wgsextract.mockups.backup`: 03:15 local time nightly; SQLite's online
  backup API, integrity checked, atomically replaced, newest 14 dated DBs kept.
- `org.wgsextract.mockups.update`: five-minute app refresh/restart.

Logs are in `logs/`, snapshots in `cache/`, backups in `backups/notes-YYYYMMDD.db`.
Run `python3 app/gui/mockups/hosting/backup.py` for an immediate backup.
For restore, boot out the server first, preserve the current DB and its
`-wal`/`-shm` files, copy the chosen backup to `notes.db`, and bootstrap again.
Check with `launchctl print user/502/org.wgsextract.mockups` and
`curl http://127.0.0.1:8765/api/health`.

On lin-mid, install the watchdog as the current user:

```sh
mkdir -p ~/.local/lib/mockup-server ~/.config/systemd/user
install -m 755 gui/mockups/hosting/watchdog.sh ~/.local/lib/mockup-server/watchdog.sh
cp gui/mockups/hosting/mockup-server-watchdog.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mockup-server-watchdog.timer
systemctl --user start mockup-server-watchdog.service
```

Requires existing noninteractive `ssh randy-mini`, curl, and agent-coord at
`~/.local/bin/agent-coord` (systemd's default PATH omits that folder).
Every five minutes it probes tailnet `/api/health`.
On failure it bootstraps the launchd service (or kickstarts an existing
one), announces the recovery through agent-coord, then checks health again.
The timer needs lin-mid online; KeepAlive handles ordinary server crashes
without it. Inspect `journalctl --user -u mockup-server-watchdog.service`.

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
    version.js          “local”; the Pages build stamps the commit here
    data/               illustrative genomes, jobs, catalogs
    render/             data-driven blocks (tables, jobs, charts, shell)
    shells/             each direction’s navigation
    annotate/           the notes tool (store, anchors, pins, panel, about.js)
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
