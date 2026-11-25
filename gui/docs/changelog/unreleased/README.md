# Unreleased desktop app changes

For each user-facing desktop app change, add an `ISSUE-slug.md` fragment here
instead of editing [the changelog](../../CHANGELOG.md). Use the issue or PR
number and a lowercase, hyphen-separated slug. Use one or more headings from
`## Added`, `## Changed`, `## Deprecated`, `## Removed`, `## Fixed`, and
`## Security`, each followed by a `- ` list. Indent continuation lines by two
spaces. Use absolute links: the same content appears on GitHub Releases and in
the app's changelog.

Run `python scripts/release_notes.py check` from the repository root.
At release, `python scripts/release_notes.py assemble X.Y.Z` adds the entries
to the dated desktop changelog and to `## Desktop app` in the joint release
notes, then removes the fragments.
