# Unreleased release notes

Each pull request with a user-facing change adds its own file here, so pull
requests never edit the same notes file and never conflict over it. The
release PR runs `python scripts/release_notes.py assemble X.Y.Z`, which writes
`docs/release-notes/vX.Y.Z.md` from these files and deletes them (see
[releasing.md](../../releasing.md)).

- **Name:** the issue or PR number, then a short slug:
  `1155-missing-ystrs.md`. One change can add several files; two changes
  never share one.
- **Content:** one or more `## Section` headings, each followed by its
  entries, written for users as in the past releases' notes. Sections are
  assembled in this order: `Changed`, `New`, `Fixes`, `Deprecated`, `Removed`.
  Within a section, entries follow the file numbers. Add app changes to
  [desktop changelog fragments](../../../gui/docs/changelog/unreleased/README.md)
  instead; release assembly puts those under `Desktop app` in the joint notes.

```markdown
## Fixes

- `lineage y-str` now calls DYS448, DYS459 and DYS589 on T2T when reads
  support a call ([#1155, missing Y-STRs](https://github.com/WGSExtract/wgsextract-cli/issues/1155)).
```

Deprecations must be announced here, under `## Deprecated`.
`python scripts/release_notes.py check` checks the names and sections; every
PR's routing job (`changes` in PR area checks) runs it. A branch that still
edits the old shared `docs/release-notes/unreleased.md` fails that check: after
merging main, keep that file deleted and move the branch's own entries into a
fragment.
