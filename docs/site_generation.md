# Static site source

Edit the Markdown pages in `site_src/`, then run:

```bash
pixi run site-build
```

The generator (`scripts/build_site.py`) writes one HTML file per page into `site/`. Generated `site/*.html` is gitignored: build it locally for previews rather than committing stale copies. The Markdown renderer is intentionally small; it supports the components already used by the current pages, plus normal headings, paragraphs, links, inline code, fenced code blocks, lists, and tables.

Common acronyms are defined once in `site_src/abbr.toml`. The generator adds matching abbreviation markup and fast site tooltips to generated HTML automatically, so Markdown pages should use plain acronym text rather than embedding abbreviation tags by hand.

The site Markdown files use YAML-compatible front matter. Run `pixi run lint` before committing; it validates Python, site front matter, and `site_src/abbr.toml`, and the same site-source check runs from pre-commit and CI.

Detailed guides live in `docs/`, indexed by task in [docs/README.md](README.md).
They are repository Markdown, not generated site pages: adding one needs a
link in that index, not a generator registration. Link it from the relevant
`site_src/` page with its GitHub URL. New website pages instead need front
matter with an `output` name and, when appropriate, an entry in `NAV_ITEMS`
in `scripts/build_site.py`.

The site build generates the command, Python API and configuration references,
plus the CLI landing page's command cards. Edit the command registry and help
catalog, API docstrings and parameter definitions, or the config schema and its
hand-written guide (`docs/data/configuration-guide.md`). Do not commit generated
reference pages. The configuration guide's links are relative to its own
directory; the build links to their published GitHub pages.

Run `pixi run -e dev python scripts/validate_site_sources.py --generated` to render
and validate the generated pages without writing HTML.

## Hand-maintained files in `site/`

The generator only writes `site/*.html`. These files are maintained by hand and must stay committed:

- `site/assets/site.css`: the stylesheet every page links to.
- `site/assets/cli-screenshot.webp`: the home page screenshot.
- `site/assets/social-preview.webp`: the Open Graph/Twitter preview image.
- `site/.nojekyll`: tells GitHub Pages to serve the files as-is without Jekyll processing.

## Deployment

`.github/workflows/pages.yml` rebuilds and publishes `site/` when site sources,
the generators, or reference inputs (including CLI source) change on `main`.
Run `pixi run site-build` for a local preview; commit only the source and
hand-maintained assets.
